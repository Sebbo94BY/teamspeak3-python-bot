"""Regression tests for runtime failures in bot and plugin control paths."""

# pylint: disable=attribute-defined-outside-init,missing-class-docstring,missing-function-docstring,redefined-outer-name,too-many-public-methods,wrong-import-position
import logging
import os
import sys
import types
import unittest
from logging.handlers import TimedRotatingFileHandler
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.makedirs("logs", exist_ok=True)


class TS3Exception(Exception):
    """Minimal SDK exception replacement for unit tests."""


class TS3QueryException(TS3Exception):
    """Minimal query exception replacement for unit tests."""


events = types.ModuleType("ts3API.Events")
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
    setattr(events, name, type(name, (), {}))
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
from modules.idle_mover import main as idle_mover
from modules.inform_team_about_newbie import main as newbie_notifier
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
        handler._pending_observers = Mock()
        with patch.object(event_handler.EventHandler.logger, "exception") as logged:
            handler._inform_observer(observer, event)
        logged.assert_called_once()
        handler._pending_observers.release.assert_called_once_with()

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
