"""Regression tests for runtime failures in bot and plugin control paths."""

# pylint: disable=attribute-defined-outside-init,missing-class-docstring,missing-function-docstring,redefined-outer-name,too-few-public-methods,too-many-public-methods,wrong-import-position
import logging
import os
import sys
import threading
import types
import unittest
from logging.handlers import TimedRotatingFileHandler
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, mock_open, patch

os.makedirs("logs", exist_ok=True)


class TS3Exception(Exception):
    """Minimal SDK exception replacement for unit tests."""


class TS3QueryException(TS3Exception):
    """Minimal query exception replacement for unit tests."""


events = types.ModuleType("ts3API.Events")


class _Event:
    """Small event stand-in matching the SDK constructor contract."""

    def __init__(self, data):
        self.data = data


for name in (
    "TextMessageEvent",
    "ChannelEditedEvent",
    "ChannelDescriptionEditedEvent",
    "ClientEnteredEvent",
    "ClientLeftEvent",
    "ClientMovedEvent",
    "ClientMovedSelfEvent",
    "ServerEditedEvent",
    "ClientKickedEvent",
    "ClientBannedEvent",
):
    setattr(events, name, type(name, (_Event,), {}))
connection = types.ModuleType("ts3API.TS3Connection")
connection.TS3QueryException = TS3QueryException
connection.TS3Connection = Mock
utilities = types.ModuleType("ts3API.utilities")
utilities.TS3Exception = TS3Exception
utilities.TS3ConnectionClosedException = TS3Exception
exception_types = types.ModuleType("ts3API.TS3QueryExceptionType")
exception_types.TS3QueryExceptionType = SimpleNamespace(
    CLIENT_NICKNAME_INUSE="nickname", CHANNEL_ALREADY_IN="channel"
)
ts3api = types.ModuleType("ts3API")
ts3api.TS3Connection = connection
sys.modules.update(
    {
        "ts3API": ts3api,
        "ts3API.Events": events,
        "ts3API.TS3Connection": connection,
        "ts3API.utilities": utilities,
        "ts3API.TS3QueryExceptionType": exception_types,
    }
)

import client_info
import event_handler
import module_loader
import teamspeak_bot
import command_handler
import log_utils
import servergroup_cache


class _Registrar:
    def add_handler(self, *_args):
        pass

    def add_observer(self, *_args):
        pass


module_loader.COMMAND_HANDLER = _Registrar()
module_loader.EVENT_HANDLER = _Registrar()
from modules import utils
from modules.afk_mover import main as afk_mover
from modules.channel_manager import main as channel_manager
from modules.channel_requester import main as channel_requester
from modules.idle_mover import main as idle_mover
from modules.inform_team_about_newbie import main as newbie_notifier
from modules.poke_client_on_channel_join import main as poke_on_join
from modules.switch_supporter_channel_status import main as supporter_status
from modules.twitch_live import main as twitch_live


class RuntimeBugTests(unittest.TestCase):
    def test_logs_rotate_daily_with_finite_retention(self):
        with TemporaryDirectory() as tempdir:
            handler = log_utils.create_log_handler(f"{tempdir}/bot.log")
            self.assertIsInstance(handler, TimedRotatingFileHandler)
            self.assertEqual(handler.backupCount, 14)
            handler.close()

    def test_twitch_requests_have_a_timeout(self):
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_api_expires_at = None

        with patch.object(
            twitch_live.request, "urlopen", side_effect=TimeoutError
        ) as urlopen:
            with self.assertRaises(TimeoutError):
                plugin.get_oauth_access_token()

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10.0)

    def test_twitch_user_ids_are_batched_and_cached(self):
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_user_ids = {}
        plugin.twitch_api_access_token = "token"
        plugin.twitch_api_client_id = "client"

        with (
            patch.object(twitch_live.request, "urlopen") as urlopen,
            patch.object(
                twitch_live.json,
                "load",
                return_value={"data": [{"login": "ada", "id": "1"}]},
            ),
        ):
            self.assertEqual(
                plugin.get_twitch_streamer_user_ids(
                    ["https://www.twitch.tv/Ada", "ada"]
                ),
                {"https://www.twitch.tv/Ada": "1", "ada": "1"},
            )
            plugin.get_twitch_streamer_user_ids(["ada"])

        urlopen.assert_called_once()

    def test_twitch_stream_statuses_are_batched(self):
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_api_access_token = "token"
        plugin.twitch_api_client_id = "client"

        with (
            patch.object(twitch_live.request, "urlopen") as urlopen,
            patch.object(
                twitch_live.json,
                "load",
                return_value={"data": [{"user_id": "1"}]},
            ),
        ):
            self.assertEqual(plugin.get_live_streamer_user_ids(["1", "2"]), {"1"})

        urlopen.assert_called_once()

    def test_twitch_invalid_descriptions_do_not_make_requests(self):
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_user_ids = {}

        with patch.object(twitch_live.request, "urlopen") as urlopen:
            self.assertEqual(
                plugin.get_twitch_streamer_user_ids(["", "not a twitch login"]), {}
            )

        urlopen.assert_not_called()

    def test_twitch_user_id_lookups_respect_the_batch_limit(self):
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_user_ids = {}
        plugin.twitch_api_access_token = "token"
        plugin.twitch_api_client_id = "client"
        descriptions = [f"streamer{index}" for index in range(101)]

        with (
            patch.object(twitch_live.request, "urlopen") as urlopen,
            patch.object(twitch_live.json, "load", return_value={"data": []}),
        ):
            result = plugin.get_twitch_streamer_user_ids(descriptions)

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(result, {description: None for description in descriptions})

    def test_twitch_user_id_lookup_propagates_network_errors(self):
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_user_ids = {}
        plugin.twitch_api_access_token = "token"
        plugin.twitch_api_client_id = "client"

        with patch.object(
            twitch_live.request,
            "urlopen",
            side_effect=twitch_live.error.URLError("down"),
        ):
            with self.assertRaises(twitch_live.error.URLError):
                plugin.get_twitch_streamer_user_ids(["ada"])

        self.assertEqual(plugin.twitch_user_ids, {})

    def test_servergroups_are_cached_until_the_refresh_interval_expires(self):
        connection = Mock()
        connection.servergrouplist.return_value = [{"sgid": "6", "name": "Admin"}]

        with patch.object(
            servergroup_cache, "monotonic", side_effect=[10.0, 10.0, 11.0]
        ):
            self.assertEqual(
                servergroup_cache.get_servergroups(connection),
                ({"sgid": "6", "name": "Admin"},),
            )
            self.assertEqual(
                servergroup_cache.get_servergroups(connection),
                ({"sgid": "6", "name": "Admin"},),
            )

        connection.servergrouplist.assert_called_once_with()

    def test_empty_command_is_ignored(self):
        handler = command_handler.CommandHandler(Mock())
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            handler.handle_command("   ", sender=7)
        send.assert_not_called()

    def test_command_permission_uses_one_client_info_for_all_handlers(self):
        connection = Mock()
        handler = command_handler.CommandHandler(connection)
        command = Mock(spec=[])
        handler.add_handler(command, "test")
        handler.add_handler(command, "test")
        clientinfo = Mock()
        clientinfo.is_in_servergroups.return_value = True

        with patch.object(
            command_handler.client_info, "ClientInfo", return_value=clientinfo
        ) as info:
            handler.handle_command("!test", sender=7)

        info.assert_called_once_with(7, connection)
        self.assertEqual(clientinfo.is_in_servergroups.call_count, 2)
        self.assertEqual(command.call_count, 2)

    def test_supplied_client_info_avoids_another_lookup(self):
        handler = command_handler.CommandHandler(Mock())
        command = Mock(spec=[])
        handler.add_handler(command, "test")
        clientinfo = Mock()
        clientinfo.is_in_servergroups.return_value = True

        with patch.object(command_handler.client_info, "ClientInfo") as info:
            handler.handle_command("!test", sender=7, clientinfo=clientinfo)

        info.assert_not_called()
        command.assert_called_once()

    def test_command_with_denied_permission_does_not_call_handler(self):
        handler = command_handler.CommandHandler(Mock())
        command = Mock(spec=[])
        handler.add_handler(command, "test")
        clientinfo = Mock()
        clientinfo.is_in_servergroups.return_value = False

        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            handler.handle_command("!test", sender=7, clientinfo=clientinfo)

        command.assert_not_called()
        send.assert_called_once()

    def test_plain_text_is_rejected(self):
        handler = command_handler.CommandHandler(Mock())
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            handler.handle_command("hello", sender=7)
        self.assertEqual(send.call_count, 2)

    def test_invalid_client_info_returns_empty_mock(self):
        connection = Mock()
        connection.servergrouplist.return_value = []
        info = client_info.ClientInfo("-1", connection)
        self.assertEqual(info.name, "")
        self.assertEqual(info.servergroups, [])

    def test_plugin_boolean_options_are_normalized(self):
        config = {
            "afk_mover": {
                "auto_start": "False",
                "enable_dry_run": "false",
                "auto_move_back": "True",
                "respect_channel_settings": "1",
            }
        }
        module_loader.normalize_plugin_options(config)
        self.assertEqual(
            config["afk_mover"],
            {
                "auto_start": False,
                "enable_dry_run": False,
                "auto_move_back": True,
                "respect_channel_settings": True,
            },
        )

    def test_observer_exceptions_are_logged_in_worker_thread(self):
        event = SimpleNamespace(data={"event": "test"})
        observer = Mock(side_effect=ValueError("broken observer"))
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._release_queue_slot = Mock()
        with patch.object(event_handler.EventHandler.logger, "exception") as logged:
            handler._inform_observer(observer, event, is_command=False)
        logged.assert_called_once()

    def test_serverquery_events_are_not_dispatched(self):
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._serverquery_client_ids = set()
        handler._serverquery_client_ids_lock = threading.Lock()
        handler.inform_all = Mock()
        event = event_handler.ClientEnteredEvent({"clid": "17", "client_type": "1"})
        event.client_id = 17
        event.client_type = "1"

        handler.on_event(None, event=event)

        handler.inform_all.assert_not_called()
        self.assertEqual(handler._serverquery_client_ids, {"17"})

    def test_later_events_for_serverquery_clients_are_not_dispatched(self):
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._serverquery_client_ids = {"17"}
        handler._serverquery_client_ids_lock = threading.Lock()
        handler.inform_all = Mock()
        event = event_handler.ClientMovedEvent({"clid": "17"})
        event.client_id = 17

        handler.on_event(None, event=event)

        handler.inform_all.assert_not_called()

    def test_serverquery_client_ids_are_released_on_leave(self):
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._serverquery_client_ids = {"17"}
        handler._serverquery_client_ids_lock = threading.Lock()
        handler.inform_all = Mock()
        event = event_handler.ClientLeftEvent({"clid": "17"})
        event.client_id = 17

        handler.on_event(None, event=event)

        handler.inform_all.assert_not_called()
        self.assertEqual(handler._serverquery_client_ids, set())

    def test_normal_client_events_are_still_dispatched(self):
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._serverquery_client_ids = {"17"}
        handler._serverquery_client_ids_lock = threading.Lock()
        handler.inform_all = Mock()
        event = event_handler.ClientEnteredEvent({"clid": "42", "client_type": "0"})
        event.client_id = 42
        event.client_type = "0"

        handler.on_event(None, event=event)

        handler.inform_all.assert_called_once_with(event)

    def test_coalesce_compatibility_observer_receives_every_event(self):
        received_sequences = []
        all_events_received = threading.Event()

        @module_loader.coalesce_events()
        def observer(event):
            received_sequences.append(event.data["sequence"])
            if len(received_sequences) == 2:
                all_events_received.set()

        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands)
        handler.add_observer(observer, event_handler.ClientMovedEvent)
        try:
            for sequence in range(2):
                handler.inform_all(
                    event_handler.ClientMovedEvent({"sequence": sequence})
                )
            self.assertTrue(all_events_received.wait(timeout=1))
        finally:
            handler.close()

        self.assertCountEqual(received_sequences, range(2))

    def test_backlogged_events_are_all_delivered_by_the_real_executor(self):
        release_observer = threading.Event()
        first_observer_started = threading.Event()
        received_sequences = []

        def observer(event):
            first_observer_started.set()
            release_observer.wait(timeout=5)
            received_sequences.append(event.data["sequence"])

        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands, event_queue_size=700)
        handler.add_observer(observer, event_handler.ClientMovedEvent)
        try:
            handler.inform_all(event_handler.ClientMovedEvent({"sequence": 0}))
            self.assertTrue(first_observer_started.wait(timeout=1))

            for sequence in range(1, 700):
                handler.inform_all(
                    event_handler.ClientMovedEvent({"sequence": sequence})
                )
        finally:
            release_observer.set()
            handler.close()

        self.assertCountEqual(received_sequences, range(700))

    def test_full_event_queue_blocks_then_delivers_the_waiting_event(self):
        release_observer = threading.Event()
        first_observer_started = threading.Event()
        second_submit_finished = threading.Event()
        queue_full_logged = threading.Event()
        received_sequences = []

        def observer(event):
            first_observer_started.set()
            release_observer.wait(timeout=5)
            received_sequences.append(event.data["sequence"])

        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands, event_queue_size=1)
        handler.add_observer(observer, event_handler.ClientMovedEvent)
        try:
            with patch.object(event_handler.EventHandler.logger, "warning") as warning:
                warning.side_effect = lambda *_args: queue_full_logged.set()
                handler.inform_all(event_handler.ClientMovedEvent({"sequence": 1}))
                self.assertTrue(first_observer_started.wait(timeout=1))
                submitter = threading.Thread(
                    target=lambda: (
                        handler.inform_all(
                            event_handler.ClientMovedEvent({"sequence": 2})
                        ),
                        second_submit_finished.set(),
                    )
                )
                submitter.start()
                self.assertFalse(second_submit_finished.wait(timeout=0.1))
                self.assertTrue(queue_full_logged.wait(timeout=1))

                release_observer.set()
                submitter.join(timeout=1)
                self.assertFalse(submitter.is_alive())
        finally:
            release_observer.set()
            handler.close()

        self.assertTrue(second_submit_finished.is_set())
        self.assertCountEqual(received_sequences, [1, 2])

    def test_full_command_queue_blocks_independently(self):
        release_observer = threading.Event()
        observer_started = threading.Event()
        submitter_finished = threading.Event()

        def observer(_event):
            observer_started.set()
            release_observer.wait(timeout=5)

        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands, command_queue_size=1)
        handler.remove_observer(commands.inform, event_handler.TextMessageEvent)
        handler.add_observer(observer, event_handler.TextMessageEvent)
        try:
            handler.inform_all(event_handler.TextMessageEvent({}))
            self.assertTrue(observer_started.wait(timeout=1))
            submitter = threading.Thread(
                target=lambda: (
                    handler.inform_all(event_handler.TextMessageEvent({})),
                    submitter_finished.set(),
                )
            )
            submitter.start()
            self.assertFalse(submitter_finished.wait(timeout=0.1))
            release_observer.set()
            submitter.join(timeout=1)
            self.assertFalse(submitter.is_alive())
        finally:
            release_observer.set()
            handler.close()

        self.assertTrue(submitter_finished.is_set())

    def test_shutdown_unblocks_a_submitter_waiting_for_queue_capacity(self):
        release_observer = threading.Event()
        observer_started = threading.Event()
        submitter_finished = threading.Event()

        def observer(_event):
            observer_started.set()
            release_observer.wait(timeout=5)

        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands, event_queue_size=1)
        handler.add_observer(observer, event_handler.ClientMovedEvent)
        handler.inform_all(event_handler.ClientMovedEvent({}))
        self.assertTrue(observer_started.wait(timeout=1))
        submitter = threading.Thread(
            target=lambda: (
                handler.inform_all(event_handler.ClientMovedEvent({})),
                submitter_finished.set(),
            )
        )
        submitter.start()
        self.assertFalse(submitter_finished.wait(timeout=0.1))
        closer = threading.Thread(target=handler.close)
        closer.start()
        self.assertTrue(submitter_finished.wait(timeout=1))
        release_observer.set()
        closer.join(timeout=1)
        self.assertFalse(closer.is_alive())

    def test_text_messages_use_the_dedicated_command_executor(self):
        observer_completed = threading.Event()
        observer_threads = []

        def observer(_event):
            observer_threads.append(threading.current_thread().name)
            observer_completed.set()

        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands)
        handler.add_observer(observer, event_handler.TextMessageEvent)
        event = event_handler.TextMessageEvent({})
        event.targetmode = "Channel"
        try:
            handler.inform_all(event)
            self.assertTrue(observer_completed.wait(timeout=1))
        finally:
            handler.close()

        self.assertEqual(len(observer_threads), 1)
        self.assertTrue(observer_threads[0].startswith("ts3-command"))

    def test_observer_lookup_is_cached_and_invalidated_when_observers_change(self):
        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands)
        first_observer = Mock()
        second_observer = Mock()
        event = event_handler.ClientMovedEvent({})
        handler.add_observer(first_observer, event_handler.ClientMovedEvent)
        try:
            first_lookup = handler.get_obs_for_event(event)
            self.assertIs(first_lookup, handler.get_obs_for_event(event))

            handler.add_observer(second_observer, event_handler.ClientMovedEvent)
            second_lookup = handler.get_obs_for_event(event)
        finally:
            handler.close()

        self.assertIsNot(first_lookup, second_lookup)
        self.assertEqual(second_lookup, frozenset({first_observer, second_observer}))

    def test_close_shuts_down_event_and_command_executors(self):
        connection = Mock()
        commands = command_handler.CommandHandler(connection)
        handler = event_handler.EventHandler(connection, commands)
        observer_called = threading.Event()
        handler.add_observer(
            lambda _event: observer_called.set(), event_handler.ClientMovedEvent
        )

        handler.close()

        handler.on_event(None, event=event_handler.ClientMovedEvent({}))

        with self.assertRaises(RuntimeError):
            handler._executor.submit(lambda: None)
        with self.assertRaises(RuntimeError):
            handler._command_executor.submit(lambda: None)
        self.assertFalse(observer_called.is_set())

    def test_memory_warning_is_rate_limited_and_uses_the_configured_limit(self):
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._memory_limit_mb = 100
        handler._last_memory_check = 0.0

        with (
            patch.object(event_handler.time, "monotonic", return_value=61.0),
            patch.object(
                event_handler.EventHandler, "_get_memory_usage_mb", return_value=80
            ),
            patch.object(event_handler.EventHandler.logger, "warning") as warning,
        ):
            handler._warn_if_memory_limit_is_near()
            handler._warn_if_memory_limit_is_near()

        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[2], 100)

    def test_memory_usage_reads_current_rss_from_proc(self):
        with (
            patch(
                "builtins.open", mock_open(read_data="Name:\tbot\nVmRSS:\t81920 kB\n")
            ),
            patch.object(event_handler.resource, "getrusage") as peak_usage,
        ):
            self.assertEqual(event_handler.EventHandler._get_memory_usage_mb(), 80)

        peak_usage.assert_not_called()

    def test_memory_usage_falls_back_to_peak_rss_without_proc(self):
        with (
            patch("builtins.open", side_effect=OSError),
            patch.object(
                event_handler.resource,
                "getrusage",
                return_value=SimpleNamespace(ru_maxrss=80 * 1024),
            ),
        ):
            self.assertEqual(event_handler.EventHandler._get_memory_usage_mb(), 80)

    def test_bot_close_stops_receiving_before_draining_and_quitting(self):
        shutdown_order = []
        connection = Mock()
        connection.stop_recv.set.side_effect = lambda: shutdown_order.append("stop")
        connection.quit.side_effect = lambda: shutdown_order.append("quit")
        handler = Mock()
        handler.close.side_effect = lambda: shutdown_order.append("drain")
        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        bot.ts3conn = connection
        bot.event_handler = handler

        bot.close()

        self.assertEqual(shutdown_order, ["stop", "drain", "quit"])
        self.assertIsNone(bot.ts3conn)
        self.assertIsNone(bot.event_handler)

    def test_stop_and_restart_shutdown_from_a_dedicated_thread(self):
        shutdown_order = []
        bot = Mock()
        bot.close.side_effect = lambda: shutdown_order.append("close")
        utils.BOT = bot
        utils.DRY_RUN = False

        with (
            patch.object(utils, "exit_all"),
            patch.object(
                utils.main,
                "restart_program",
                side_effect=lambda: shutdown_order.append("restart"),
            ),
            patch.object(utils, "_shutdown_bot") as shutdown,
        ):
            utils.stop_bot(1, "!stop")
            utils.restart_bot(1, "!restart")

        shutdown.assert_has_calls([call(), call(restart=True)])

        with patch.object(
            utils.main,
            "restart_program",
            side_effect=lambda: shutdown_order.append("restart"),
        ):
            thread = utils._shutdown_bot(restart=True)
            self.assertEqual(thread.name, "ts3-shutdown")
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())

        self.assertEqual(shutdown_order, ["close", "restart"])

    def test_multimove_moves_client_without_casting_client_dictionary(self):
        connection = Mock()
        connection.channelfind.side_effect = [
            [{"cid": "1"}],
            [{"cid": "2"}],
        ]
        connection.clientlist.return_value = [
            {"client_type": "0", "cid": "1", "clid": "42", "client_nickname": "Ada"}
        ]
        utils.BOT = SimpleNamespace(ts3conn=connection)
        utils.DRY_RUN = False
        utils.multi_move(7, '!multimove "source" "target"')
        connection.clientmove.assert_called_once_with(2, 42)

    def test_multimove_rejects_ambiguous_target_channel(self):
        connection = Mock()
        connection.channelfind.side_effect = [
            [{"cid": "1"}],
            [{"cid": "2"}, {"cid": "3"}],
        ]
        utils.BOT = SimpleNamespace(ts3conn=connection)
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            utils.multi_move(7, '!multimove "source" "target"')
        connection.clientmove.assert_not_called()
        self.assertEqual(send.call_count, 2)

    def test_afk_mover_continues_after_unexpected_channel_info_error(self):
        mover = afk_mover.AfkMover.__new__(afk_mover.AfkMover)
        mover.client_channels = {"42": "1"}
        mover.afk_channel = "9"
        mover.afk_list = [{"clid": "42", "cid": "9", "client_away": "0"}]
        mover.logger = Mock()
        error = TS3QueryException()
        error.id = 999
        mover.ts3conn = Mock()
        mover.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        mover.ts3conn._send.side_effect = error
        mover.fallback_action = Mock()
        mover.move_all_back()
        mover.fallback_action.assert_called_once_with(42)

    def test_stopping_a_plugin_waits_for_its_worker(self):
        worker = Mock()
        afk_mover.PLUGIN_INFO = worker
        afk_mover.PLUGIN_STOPPER.clear()
        afk_mover.stop_plugin()
        self.assertTrue(afk_mover.PLUGIN_STOPPER.is_set())
        worker.join.assert_called_once_with()
        self.assertIsNone(afk_mover.PLUGIN_INFO)

    def test_setup_failure_does_not_continue_with_none_connection(self):
        with (
            patch.object(teamspeak_bot.Ts3Bot, "connect"),
            patch.object(teamspeak_bot.Ts3Bot, "setup_bot"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Bot setup failed"):
                teamspeak_bot.Ts3Bot(
                    host="host",
                    port="10011",
                    serverid="1",
                    user="user",
                    password="password",
                    defaultchannel="default",
                    botname="bot",
                    logger=logging.getLogger("test"),
                    plugins={},
                )

    def test_missing_default_channel_raises_a_clear_error(self):
        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        bot.ts3conn = Mock()
        bot.ts3conn.channelfind.return_value = []
        with self.assertRaisesRegex(LookupError, "No channel found"):
            bot.get_channel_id("missing")

    def test_plugins_warn_instead_of_raising_for_missing_channels(self):
        plugin_types = (
            (afk_mover.AfkMover, "get_channel_by_name"),
            (idle_mover.IdleMover, "get_channel_by_name"),
            (newbie_notifier.InformTeamAboutNewbie, "get_channel_by_name"),
            (channel_manager.ChannelManager, "get_channel_id_by_name_pattern"),
            (channel_requester.ChannelRequester, "get_channel_by_name"),
            (poke_on_join.PokeClientOnChannelJoin, "get_channel_by_name"),
            (supporter_status.SwitchSupporterChannelStatus, "get_channel_by_name"),
        )

        for plugin_type, lookup_method in plugin_types:
            with self.subTest(plugin=plugin_type.__name__):
                plugin = plugin_type.__new__(plugin_type)
                plugin.logger = Mock()
                plugin.ts3conn = Mock()
                plugin.ts3conn.channelfind.return_value = []

                self.assertIsNone(getattr(plugin, lookup_method)("missing"))
                plugin.logger.warning.assert_called_once()

    def test_channel_requester_ignores_only_the_missing_channel_configuration(self):
        requester = channel_requester.ChannelRequester.__new__(
            channel_requester.ChannelRequester
        )
        requester.logger = Mock()
        requester.ts3conn = Mock()
        requester.ts3conn.channelfind.side_effect = [[], [{"cid": "42"}]]

        configs = requester.parse_channel_settings(
            {
                "missing.main_channel_name": "Missing",
                "available.main_channel_name": "Available",
            }
        )

        self.assertEqual(
            configs,
            [
                {
                    "main_channel_name": "Available",
                    "main_channel_cid": "42",
                }
            ],
        )
        requester.logger.warning.assert_called_once()

    def test_afk_mover_stays_inactive_without_its_target_channel(self):
        mover = afk_mover.AfkMover.__new__(afk_mover.AfkMover)
        mover.afk_channel = None
        mover.logger = Mock()
        mover.stopped = Mock()

        mover.auto_move_all()

        mover.stopped.wait.assert_not_called()
        mover.logger.warning.assert_called_once()

    def test_default_channel_lookup_returns_matching_channel_id(self):
        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        bot.ts3conn = Mock()
        bot.ts3conn.channelfind.return_value = [{"cid": "42"}]
        self.assertEqual(bot.get_channel_id("default"), 42)

    def test_partially_constructed_bot_can_be_destroyed(self):
        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        # pylint: disable=unnecessary-dunder-call
        bot.__del__()

    def test_idle_mover_handles_unexpected_channel_info_error(self):
        mover = idle_mover.IdleMover.__new__(idle_mover.IdleMover)
        mover.logger = Mock()
        mover.idling_clients = {42: 1}
        mover.get_back_list = Mock(return_value={42: 1})
        mover.fallback_action = Mock()
        error = TS3QueryException()
        error.id = 999
        mover.ts3conn = Mock()
        mover.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        mover.ts3conn._send.side_effect = error
        mover.move_all_back()
        mover.fallback_action.assert_called_once_with(42)

    def test_channel_manager_handles_unexpected_channel_info_error(self):
        manager = channel_manager.ChannelManager.__new__(channel_manager.ChannelManager)
        manager.logger = Mock()
        manager.channel_configs = []
        manager.managed_channels = []
        manager.find_channels_by_prefix = Mock(return_value=[])
        error = TS3QueryException()
        error.id = 999
        manager.ts3conn = Mock()
        manager.ts3conn._send.side_effect = error
        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id=1))

    def test_team_poke_renders_each_recipient_name(self):
        notifier = newbie_notifier.InformTeamAboutNewbie.__new__(
            newbie_notifier.InformTeamAboutNewbie
        )
        notifier.logger = Mock()
        notifier.ts3conn = Mock()
        notifier.ts3conn.clientlist.return_value = [
            {
                "client_type": "0",
                "client_database_id": "1",
                "clid": "10",
                "client_nickname": "Ada",
            },
            {
                "client_type": "0",
                "client_database_id": "2",
                "clid": "20",
                "client_nickname": "Lin",
            },
        ]
        notifier.get_team_member_list = Mock(return_value=["1", "2"])
        notifier.team_poke_message = "%u: %n (%c online)"
        original_dry_run = newbie_notifier.DRY_RUN
        newbie_notifier.DRY_RUN = False
        try:
            notifier.poke_team(SimpleNamespace(client_name="Newbie"))
        finally:
            newbie_notifier.DRY_RUN = original_dry_run
        self.assertEqual(
            notifier.ts3conn.clientpoke.call_args_list,
            [
                (("10", "Ada: Newbie (2 online)"),),
                (("20", "Lin: Newbie (2 online)"),),
            ],
        )

    def test_team_poke_skips_when_no_team_member_is_online(self):
        notifier = newbie_notifier.InformTeamAboutNewbie.__new__(
            newbie_notifier.InformTeamAboutNewbie
        )
        notifier.logger = Mock()
        notifier.ts3conn = Mock()
        notifier.ts3conn.clientlist.return_value = []
        notifier.get_team_member_list = Mock(return_value=[])
        notifier.team_poke_message = "%u: %n"
        notifier.poke_team(SimpleNamespace(client_name="Newbie"))
        notifier.ts3conn.clientpoke.assert_not_called()

    def test_serverquery_name_is_treated_as_text(self):
        notifier = newbie_notifier.InformTeamAboutNewbie.__new__(
            newbie_notifier.InformTeamAboutNewbie
        )
        notifier.logger = Mock()
        notifier.get_servergroups_by_client = Mock()
        notifier.check_joined_client(
            SimpleNamespace(
                client_uid="ServerQuery", client_name="query_1", client_dbid="1"
            )
        )
        notifier.get_servergroups_by_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
