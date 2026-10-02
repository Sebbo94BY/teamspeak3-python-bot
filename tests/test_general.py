"""Unit tests for shared bot, command, event, and infrastructure code."""

# pylint: disable=attribute-defined-outside-init,missing-class-docstring,missing-function-docstring,redefined-outer-name,too-few-public-methods,too-many-public-methods,wrong-import-position
import logging
import os
import sys
import threading
import unittest
from logging.handlers import TimedRotatingFileHandler
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import (
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

os.makedirs("logs", exist_ok=True)

install_ts3api_stubs()

import client_info
import event_handler
import helpers
import main
import module_loader
import teamspeak_bot
import command_handler
import log_utils
import servergroup_cache

module_loader = configure_module_loader()


class GeneralTests(unittest.TestCase):
    def test_logs_rotate_daily_with_finite_retention(self):
        with TemporaryDirectory() as tempdir:
            handler = log_utils.create_log_handler(f"{tempdir}/bot.log")
            self.assertIsInstance(handler, TimedRotatingFileHandler)
            self.assertEqual(handler.backupCount, 14)
            handler.close()

    def test_strtobool_accepts_supported_values_and_rejects_unknown_values(self):
        self.assertTrue(helpers.strtobool("YES"))
        self.assertFalse(helpers.strtobool("off"))

        with self.assertRaises(ValueError):
            helpers.strtobool("maybe")

    def test_servergroup_cache_can_be_invalidated(self):
        connection = Mock()
        connection.servergrouplist.side_effect = [
            [{"sgid": "1"}],
            [{"sgid": "2"}],
        ]

        self.assertEqual(
            servergroup_cache.get_servergroups(connection), ({"sgid": "1"},)
        )
        servergroup_cache.invalidate_servergroups(connection)
        self.assertEqual(
            servergroup_cache.get_servergroups(connection), ({"sgid": "2"},)
        )

    def test_send_msg_to_client_uses_private_message_mode(self):
        connection = Mock()

        teamspeak_bot.send_msg_to_client(connection, 42, "hello")

        connection.sendtextmessage.assert_called_once_with(
            targetmode=1, target=42, msg="hello"
        )

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

    def test_client_info_resolves_properties_and_servergroup_patterns(self):
        connection = Mock()
        connection.clientinfo.return_value = {
            "client_nickname": "Ada",
            "client_servergroups": "1,2",
            "client_description": "https://www.twitch.tv/ada",
            "cid": "7",
        }
        connection.servergrouplist.return_value = [
            {"sgid": "1", "name": "Support"},
            {"sgid": "2", "name": "Moderator"},
        ]

        info = client_info.ClientInfo("42", connection)

        self.assertEqual(info.name, "Ada")
        self.assertEqual(info.channel_id, "7")
        self.assertEqual(info.servergroup_ids, ("1", "2"))
        self.assertTrue(info.is_in_servergroups("Support|Moderator"))
        self.assertFalse(info.is_in_servergroups("Administrator"))
        self.assertEqual(info.description, "https://www.twitch.tv/ada")

    def test_client_info_handles_empty_servergroups_and_unknown_attributes(self):
        connection = Mock()
        connection.clientinfo.return_value = {"client_servergroups": ""}
        connection.servergrouplist.return_value = []
        info = client_info.ClientInfo("42", connection)
        self.assertEqual(info.servergroups, [])
        self.assertEqual(info.servergroup_ids, ())
        self.assertEqual(info.ip, "")
        with self.assertRaises(AttributeError):
            _ = info.not_a_client_property

    def test_command_permissions_support_explicit_groups(self):
        handler = command_handler.CommandHandler(Mock())
        command = Mock(spec=[])
        command.allowed_groups = ("Moderator",)
        clientinfo = Mock()
        clientinfo.is_in_servergroups.return_value = True
        self.assertTrue(handler.check_permission(command, clientinfo))
        self.assertFalse(
            handler.check_permission(
                Mock(spec=[]), Mock(is_in_servergroups=Mock(return_value=False))
            )
        )

    def test_command_inform_only_handles_private_messages_not_self(self):
        handler = command_handler.CommandHandler(Mock())
        private_event = command_handler.TextMessageEvent({})
        private_event.targetmode = "Private"
        private_event.invoker_id = 7
        private_event.message = "!help"
        handler.ts3conn.whoami.return_value = {"client_id": "8"}
        with (
            patch.object(
                command_handler.client_info, "ClientInfo", return_value=Mock(name="Ada")
            ),
            patch.object(handler, "handle_command") as handle,
        ):
            handler.inform(private_event)
        handle.assert_called_once()

        private_event.invoker_id = 8
        with patch.object(handler, "handle_command") as handle:
            handler.inform(private_event)
        handle.assert_not_called()

        private_event.targetmode = "Channel"
        handler.inform(private_event)

    def test_module_decorators_and_exit_all_keep_going_after_failures(self):
        observer = Mock()
        module_loader.EVENT_HANDLER = Mock()
        registered = module_loader.event(SimpleNamespace)(observer)
        self.assertIs(registered, observer)
        module_loader.EVENT_HANDLER.add_observer.assert_called_once_with(
            observer, SimpleNamespace
        )

        command = Mock()
        module_loader.COMMAND_HANDLER = Mock()
        self.assertIs(module_loader.command("a", "b")(command), command)
        self.assertEqual(module_loader.COMMAND_HANDLER.add_handler.call_count, 2)
        self.assertEqual(
            module_loader.group("Admin")(command).allowed_groups, ("Admin",)
        )

        failing = Mock(side_effect=RuntimeError("broken"))
        succeeding = Mock()
        with patch.object(module_loader, "exits", [failing, succeeding]):
            module_loader.exit_all()
        succeeding.assert_called_once_with()

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

    def test_unknown_command_returns_help_messages(self):
        handler = command_handler.CommandHandler(Mock())

        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            handler.handle_command("!does_not_exist", sender=7)

        self.assertEqual(send.call_count, 2)
        self.assertIn("does_not_exist", send.call_args_list[0].args[-1])

    def test_two_word_command_is_dispatched_with_original_message(self):
        handler = command_handler.CommandHandler(Mock())
        command = Mock(spec=[])
        handler.add_handler(command, "chatgpt ask")
        clientinfo = Mock()
        clientinfo.is_in_servergroups.return_value = True

        handler.handle_command(
            "!chatgpt ask Explain testing", sender=7, clientinfo=clientinfo
        )

        command.assert_called_once_with(7, "!chatgpt ask Explain testing")

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

    def test_load_modules_imports_plugins_and_calls_configured_setups(self):
        bot = SimpleNamespace(event_handler=Mock(), command_handler=Mock())
        imported_plugin = SimpleNamespace()
        setup_values = []

        def setup_plugin(**kwargs):
            setup_values.append(kwargs)

        setup_plugin.__module__ = "tests.fake_plugin_setup"
        setup_module = SimpleNamespace(pluginname="fake")
        config = {
            "Plugins": {"FakePlugin": "fake"},
            "fake": {"enable_dry_run": "false"},
        }

        with (
            patch.object(module_loader, "setups", [setup_plugin]),
            patch.object(module_loader, "plugin_modules", {}),
            patch.object(
                module_loader.importlib,
                "import_module",
                return_value=imported_plugin,
            ) as import_module,
            patch.dict(sys.modules, {"tests.fake_plugin_setup": setup_module}),
        ):
            module_loader.load_modules(bot, config)
            self.assertEqual(
                module_loader.plugin_modules["FakePlugin"], imported_plugin
            )

        import_module.assert_called_once_with("modules.fake", package="modules")
        self.assertEqual(imported_plugin.pluginname, "fake")
        self.assertEqual(
            setup_values,
            [{"ts3bot": bot, "enable_dry_run": False}],
        )
        self.assertEqual(config, {})

    def test_exit_plugin_decorator_returns_registered_function(self):
        function = Mock()

        with patch.object(module_loader, "exits", []):
            decorated = module_loader.exit_plugin(function)

        self.assertIs(decorated, function)

    def test_event_coalescing_validates_scope_and_preserves_observer(self):
        with self.assertRaisesRegex(ValueError, "scope"):
            module_loader.coalesce_events("per_channel")

        def observer(_event):
            pass

        decorated = module_loader.coalesce_events("client")(observer)
        self.assertIs(decorated, observer)
        self.assertFalse(hasattr(decorated, "event_coalesce_scope"))

    @staticmethod
    def _new_event_handler(
        observer=None,
        event_queue_size=10,
        command_queue_size=10,
        event_type=SimpleNamespace,
    ):
        """Create an EventHandler test double with its runtime state initialized."""
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler.observers = {event_type: {observer}} if observer is not None else {}
        handler._observers_by_event_type = {}
        handler._observers_lock = threading.RLock()
        handler._accepting_events = True
        handler._queue_condition = threading.Condition()
        handler._event_queue_size = event_queue_size
        handler._command_queue_size = command_queue_size
        handler._pending_event_jobs = 0
        handler._pending_command_jobs = 0
        handler._event_queue_warning_logged = False
        handler._command_queue_warning_logged = False
        handler._memory_limit_mb = 0
        handler._last_memory_check = 0.0
        handler._executor = Mock()
        handler._command_executor = Mock()
        return handler

    def test_event_handler_close_waits_for_worker_shutdown(self):
        handler = self._new_event_handler()

        handler.close()

        self.assertFalse(handler._accepting_events)
        handler._executor.shutdown.assert_called_once_with(wait=True)
        handler._command_executor.shutdown.assert_called_once_with(wait=True)

    def test_event_handler_rejects_invalid_queue_and_worker_sizes(self):
        invalid_options = (
            {"event_queue_size": 0},
            {"command_queue_size": 0},
            {"event_workers": 0},
            {"command_workers": 0},
        )

        for options in invalid_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                event_handler.EventHandler(Mock(), Mock(), **options)

    def test_text_messages_use_the_dedicated_command_executor(self):
        observer = Mock()
        handler = self._new_event_handler(
            observer, event_type=event_handler.TextMessageEvent
        )
        event = event_handler.TextMessageEvent({})

        handler.inform_all(event)

        handler._executor.submit.assert_not_called()
        handler._command_executor.submit.assert_called_once_with(
            handler._inform_observer, observer, event, True
        )
        self.assertEqual(handler._pending_command_jobs, 1)

    def test_event_handler_dispatches_all_known_event_types_and_unknown_events(self):
        handler = event_handler.EventHandler.__new__(event_handler.EventHandler)
        handler._serverquery_client_ids = set()
        handler._serverquery_client_ids_lock = threading.Lock()
        handler.inform_all = Mock()
        event_types = (
            event_handler.TextMessageEvent,
            event_handler.ChannelEditedEvent,
            event_handler.ChannelDescriptionEditedEvent,
            event_handler.ClientEnteredEvent,
            event_handler.ClientLeftEvent,
            event_handler.ClientMovedEvent,
            event_handler.ClientMovedSelfEvent,
            event_handler.ServerEditedEvent,
        )
        for event_type in event_types:
            event = event_type({})
            if event_type is event_handler.ServerEditedEvent:
                event.changed_properties = {}
            event.client_type = "0"
            handler.on_event(None, event=event)
        handler.on_event(None, event=SimpleNamespace(data={}))
        self.assertEqual(handler.inform_all.call_count, len(event_types) + 1)

    def test_event_handler_releases_queue_slot_when_submission_fails(self):
        observer = Mock()
        event = SimpleNamespace(client_id=7, data={})
        handler = self._new_event_handler(observer)
        handler._executor.submit.side_effect = RuntimeError("closed")

        handler.inform_all(event)

        self.assertEqual(handler._pending_event_jobs, 0)
        handler._executor.submit.assert_called_once_with(
            handler._inform_observer, observer, event, False
        )

    def test_observer_exceptions_are_logged_in_worker_thread(self):
        event = SimpleNamespace(data={"event": "test"})
        observer = Mock(side_effect=ValueError("broken observer"))
        handler = self._new_event_handler()
        handler._pending_event_jobs = 1
        with patch.object(event_handler.EventHandler.logger, "exception") as logged:
            handler._inform_observer(observer, event, False)
        logged.assert_called_once()
        self.assertEqual(handler._pending_event_jobs, 0)

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

    def test_observer_receives_every_event_for_a_client(self):
        event = SimpleNamespace(client_id=7, data={})
        observer = Mock()
        handler = self._new_event_handler(observer)

        handler.inform_all(event)
        handler.inform_all(event)

        self.assertEqual(handler._executor.submit.call_count, 2)
        self.assertEqual(handler._pending_event_jobs, 2)

    def test_observer_receives_events_for_different_clients(self):
        observer = Mock()
        handler = self._new_event_handler(observer)

        handler.inform_all(SimpleNamespace(client_id=7, data={}))
        handler.inform_all(SimpleNamespace(client_id=8, data={}))

        self.assertEqual(handler._executor.submit.call_count, 2)

    def test_queue_slot_is_released_after_a_job_finishes(self):
        handler = self._new_event_handler(event_queue_size=1)

        self.assertTrue(handler._acquire_queue_slot(False))
        self.assertEqual(handler._pending_event_jobs, 1)

        handler._release_queue_slot(False)

        self.assertEqual(handler._pending_event_jobs, 0)

    def test_full_queue_applies_backpressure_until_a_slot_is_released(self):
        handler = self._new_event_handler(event_queue_size=1)
        self.assertTrue(handler._acquire_queue_slot(False))

        started = threading.Event()
        acquired = threading.Event()
        result = []

        def wait_for_slot():
            started.set()
            result.append(handler._acquire_queue_slot(False))
            acquired.set()

        worker = threading.Thread(target=wait_for_slot)
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        self.assertFalse(acquired.wait(timeout=0.05))

        handler._release_queue_slot(False)

        self.assertTrue(acquired.wait(timeout=1))
        self.assertEqual(result, [True])
        handler._release_queue_slot(False)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())

    def test_closed_event_handler_rejects_new_events(self):
        observer = Mock()
        handler = self._new_event_handler(observer)
        handler.close()

        handler.inform_all(SimpleNamespace(data={}))

        handler._executor.submit.assert_not_called()
        self.assertEqual(handler._pending_event_jobs, 0)

    def test_close_unblocks_producer_waiting_for_a_full_queue(self):
        handler = self._new_event_handler(event_queue_size=1)
        self.assertTrue(handler._acquire_queue_slot(False))

        started = threading.Event()
        unblocked = threading.Event()
        result = []

        def wait_for_slot():
            started.set()
            result.append(handler._acquire_queue_slot(False))
            unblocked.set()

        worker = threading.Thread(target=wait_for_slot)
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        self.assertFalse(unblocked.wait(timeout=0.05))

        handler.close()

        self.assertTrue(unblocked.wait(timeout=1))
        self.assertEqual(result, [False])
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        handler._release_queue_slot(False)

    def test_observer_cache_is_invalidated_when_observers_change(self):
        first_observer = Mock()
        second_observer = Mock()
        handler = self._new_event_handler(first_observer)
        event = SimpleNamespace(data={})

        initial = handler.get_obs_for_event(event)
        self.assertEqual(initial, {first_observer})

        handler.add_observer(second_observer, SimpleNamespace)
        with_second_observer = handler.get_obs_for_event(event)
        self.assertEqual(with_second_observer, {first_observer, second_observer})

        handler.remove_observer(first_observer, SimpleNamespace)
        without_first_observer = handler.get_obs_for_event(event)
        self.assertEqual(without_first_observer, {second_observer})

    def test_memory_warning_is_emitted_only_when_limit_is_near(self):
        handler = self._new_event_handler()
        handler._memory_limit_mb = 100

        with (
            patch.object(event_handler.time, "monotonic", side_effect=[100.0, 100.0]),
            patch.object(
                event_handler.EventHandler, "_get_memory_usage_mb", return_value=80
            ),
            patch.object(event_handler.EventHandler.logger, "warning") as warning,
        ):
            handler._warn_if_memory_limit_is_near()

        warning.assert_called_once()

    def test_memory_warning_is_skipped_when_disabled_or_not_due(self):
        handler = self._new_event_handler()
        with patch.object(
            event_handler.EventHandler, "_get_memory_usage_mb"
        ) as memory_usage:
            handler._warn_if_memory_limit_is_near()
        memory_usage.assert_not_called()

        handler._memory_limit_mb = 100
        handler._last_memory_check = 100.0
        with (
            patch.object(event_handler.time, "monotonic", return_value=120.0),
            patch.object(
                event_handler.EventHandler, "_get_memory_usage_mb"
            ) as memory_usage,
        ):
            handler._warn_if_memory_limit_is_near()
        memory_usage.assert_not_called()

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

    def test_bot_config_parsing_and_connection_options(self):
        logger = Mock()
        with patch.object(
            teamspeak_bot.configparser.ConfigParser, "read", return_value=[]
        ):
            with self.assertRaises(SystemExit):
                teamspeak_bot.Ts3Bot.parse_config(logger)
        with patch.object(
            teamspeak_bot.configparser.ConfigParser, "read", return_value=["config.ini"]
        ):
            with patch.object(
                teamspeak_bot.configparser.ConfigParser,
                "has_section",
                side_effect=[False],
            ):
                with self.assertRaises(SystemExit):
                    teamspeak_bot.Ts3Bot.parse_config(logger)

        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        bot.host = "host"
        bot.port = 10011
        bot.is_ssh = False
        bot.user = "user"
        bot.password = "password"
        bot.accept_all_keys = False
        bot.host_key_file = None
        bot.use_system_hosts = False
        bot.sshtimeout = None
        bot.sshtimeoutlimit = 3
        bot.logger = Mock()
        connection = Mock()
        with patch.object(
            teamspeak_bot.ts3API.TS3Connection, "TS3Connection", return_value=connection
        ):
            bot.connect()
        self.assertIs(bot.ts3conn, connection)

        with patch.object(
            teamspeak_bot.ts3API.TS3Connection,
            "TS3Connection",
            side_effect=TS3QueryException(),
        ):
            with self.assertRaises(TS3QueryException):
                bot.connect()

    def test_bot_setup_handles_nickname_and_channel_already_in_use(self):
        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        bot.ts3conn = Mock()
        bot.sid = "1"
        bot.bot_name = "Bot"
        bot.default_channel = "Lobby"
        bot.logger = Mock()
        bot.event_workers = 2
        bot.command_workers = 1
        bot.event_queue_size = 50
        bot.command_queue_size = 10
        bot.memory_limit_mb = 128
        bot.ts3conn.whoami.return_value = {"client_id": "99"}
        nickname_error = TS3QueryException()
        nickname_error.type = teamspeak_bot.TS3QueryExceptionType.CLIENT_NICKNAME_INUSE
        move_error = TS3QueryException()
        move_error.type = teamspeak_bot.TS3QueryExceptionType.CHANNEL_ALREADY_IN
        bot.ts3conn.clientupdate.side_effect = nickname_error
        bot.get_channel_id = Mock(return_value=7)
        bot.ts3conn.clientmove.side_effect = move_error
        with (
            patch.object(command_handler, "CommandHandler", return_value=Mock()),
            patch.object(
                event_handler, "EventHandler", return_value=Mock()
            ) as event_handler_class,
        ):
            bot.setup_bot()
        bot.ts3conn.register_for_server_events.assert_called_once()
        event_handler_class.assert_called_once_with(
            ts3conn=bot.ts3conn,
            command_handler=bot.command_handler,
            event_workers=2,
            command_workers=1,
            event_queue_size=50,
            command_queue_size=10,
            memory_limit_mb=128,
        )

    def test_bot_rejects_invalid_runtime_limits(self):
        invalid_options = (
            ("eventworkers", 0, "EventWorkers"),
            ("commandworkers", 0, "CommandWorkers"),
            ("eventqueuesize", 0, "EventQueueSize"),
            ("commandqueuesize", 0, "CommandQueueSize"),
            ("memorylimitmb", -1, "MemoryLimitMB"),
        )
        base_options = {
            "host": "host",
            "port": "10011",
            "serverid": "1",
            "user": "user",
            "password": "password",
            "defaultchannel": "default",
            "botname": "bot",
            "logger": logging.getLogger("test"),
            "plugins": {},
        }

        for option, value, option_name in invalid_options:
            with (
                self.subTest(option=option),
                self.assertRaisesRegex(ValueError, option_name),
            ):
                teamspeak_bot.Ts3Bot(**{**base_options, option: value})

    def test_bot_close_stops_connection_and_event_handler_idempotently(self):
        bot = teamspeak_bot.Ts3Bot.__new__(teamspeak_bot.Ts3Bot)
        connection = Mock()
        event_handler_instance = Mock()
        bot.ts3conn = connection
        bot.event_handler = event_handler_instance

        bot.close()

        connection.stop_recv.set.assert_called_once_with()
        event_handler_instance.close.assert_called_once_with()
        connection.quit.assert_called_once_with()
        self.assertIsNone(bot.ts3conn)
        self.assertIsNone(bot.event_handler)

        # Calling close a second time must not attempt to close either resource again.
        bot.close()
        connection.stop_recv.set.assert_called_once_with()
        event_handler_instance.close.assert_called_once_with()
        connection.quit.assert_called_once_with()

    def test_main_exception_handler_and_entrypoint_configuration(self):
        logger = Mock()
        with patch.object(main, "LOGGER", logger):
            main.exception_handler(ValueError, ValueError("bad"), None)
        logger.error.assert_called_once()

        original_thread_run = threading.Thread.run
        with (
            patch.object(
                main.teamspeak_bot.Ts3Bot, "parse_config", return_value={"config": 1}
            ),
            patch.object(
                main.teamspeak_bot.Ts3Bot, "bot_from_config", return_value="bot"
            ),
            patch.object(main.os, "makedirs", side_effect=FileExistsError),
            patch.object(main, "LOGGER", None),
        ):
            try:
                main.main()
            finally:
                threading.Thread.run = original_thread_run
        self.assertEqual(main.BOT, "bot")

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


if __name__ == "__main__":
    unittest.main()
