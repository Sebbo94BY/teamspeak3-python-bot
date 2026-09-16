"""Regression tests for runtime failures in bot and plugin control paths."""
import logging
import os
import sys
import types
import unittest
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


class _Registrar:
    def add_handler(self, *_args):
        pass

    def add_observer(self, *_args):
        pass


module_loader.COMMAND_HANDLER = _Registrar()
module_loader.EVENT_HANDLER = _Registrar()
from modules import utils
from modules.afk_mover import main as afk_mover


class RuntimeBugTests(unittest.TestCase):
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
        with patch.object(event_handler.EventHandler.logger, "exception") as logged:
            event_handler.EventHandler._inform_observer(observer, event)
        logged.assert_called_once()

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
        with patch.object(teamspeak_bot.Ts3Bot, "connect"), patch.object(
            teamspeak_bot.Ts3Bot, "setup_bot"
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


if __name__ == "__main__":
    unittest.main()
