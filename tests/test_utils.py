"""Unit tests for the general utility plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import (
    TS3Exception,
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules import utils
import teamspeak_bot


class UtilsTests(unittest.TestCase):
    def test_send_version_and_command_list_send_messages(self):
        connection = Mock()
        utils.BOT = SimpleNamespace(
            ts3conn=connection,
            command_handler=SimpleNamespace(handlers={"!help": Mock()}),
        )
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            utils.send_version(7, "")
            utils.get_command_list(7, "")
        self.assertEqual(send.call_count, 3)

    def test_stop_and_restart_honor_dry_run_and_schedule_shutdown(self):
        utils.BOT = SimpleNamespace(close=Mock())
        utils.DRY_RUN = True
        with (
            patch.object(utils, "exit_all") as exit_all,
            patch.object(utils.main, "restart_program") as restart,
            patch.object(utils, "_shutdown_bot") as shutdown,
        ):
            utils.stop_bot(7, "")
            utils.restart_bot(7, "")
        exit_all.assert_not_called()
        restart.assert_not_called()
        shutdown.assert_not_called()

        utils.DRY_RUN = False
        with (
            patch.object(utils, "exit_all") as exit_all,
            patch.object(utils.main, "restart_program") as restart,
            patch.object(utils, "_shutdown_bot") as shutdown,
        ):
            utils.stop_bot(7, "")
            utils.restart_bot(7, "")
        self.assertEqual(exit_all.call_count, 2)
        shutdown.assert_any_call()
        shutdown.assert_any_call(restart=True)
        self.assertEqual(shutdown.call_count, 2)

    def test_shutdown_closes_bot_and_restarts_in_shutdown_thread(self):
        bot = SimpleNamespace(close=Mock())
        utils.BOT = bot

        with patch.object(utils.main, "restart_program") as restart:
            shutdown_thread = utils._shutdown_bot(restart=True)
            shutdown_thread.join(timeout=1)

        self.assertFalse(shutdown_thread.is_alive())
        bot.close.assert_called_once_with()
        restart.assert_called_once_with()

    def test_multimove_moves_clients_between_matching_channels(self):
        connection = Mock()
        connection.channelfind.side_effect = [[{"cid": "1"}], [{"cid": "2"}]]
        connection.clientlist.return_value = [
            {"client_type": "0", "cid": "1", "clid": "42"}
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

        with (
            patch.object(teamspeak_bot, "send_msg_to_client") as send,
            patch.object(utils, "logger"),
        ):
            utils.multi_move(7, '!multimove "source" "target"')

        connection.clientmove.assert_not_called()
        self.assertEqual(send.call_count, 2)

    def test_multimove_supports_all_sources_and_dry_run(self):
        connection = Mock()
        connection.channellist.return_value = [{"cid": "1"}, {"cid": "2"}]
        connection.channelfind.return_value = [{"cid": "3"}]
        connection.clientlist.return_value = [
            {"client_type": "1", "cid": "1", "clid": "10"},
            {"client_type": "0", "cid": "1", "clid": "11"},
            {"client_type": "0", "cid": "2", "clid": "12"},
            {"client_type": "0", "cid": "9", "clid": "13"},
        ]
        utils.BOT = SimpleNamespace(ts3conn=connection)
        with patch.object(utils, "DRY_RUN", True):
            utils.multi_move(7, '!multimove "all" "target"')
        connection.clientmove.assert_not_called()

        utils.DRY_RUN = False
        utils.multi_move(7, '!multimove "source;all" "target"')
        self.assertEqual(connection.clientmove.call_count, 2)

    def test_multimove_reports_source_and_target_lookup_errors(self):
        connection = Mock()
        utils.BOT = SimpleNamespace(ts3conn=connection)
        connection.channelfind.side_effect = TS3Exception()
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            utils.multi_move(7, '!multimove "source" "target"')
        send.assert_called_once()

        connection.channelfind.side_effect = [[], [{"cid": "2"}]]
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            utils.multi_move(7, '!multimove "source" "target"')
        self.assertEqual(send.call_count, 2)

    def test_multimove_handles_empty_clients_and_already_in_channel(self):
        connection = Mock()
        connection.channelfind.side_effect = [[{"cid": "1"}], [{"cid": "2"}]]
        connection.clientlist.return_value = []
        utils.BOT = SimpleNamespace(ts3conn=connection)
        with patch.object(teamspeak_bot, "send_msg_to_client") as send:
            utils.multi_move(7, '!multimove "source" "target"')
        self.assertEqual(send.call_count, 1)

        connection.channelfind.side_effect = [[{"cid": "1"}], [{"cid": "2"}]]
        connection.clientlist.return_value = [
            {"client_type": "0", "cid": "1", "clid": "42"}
        ]
        error = TS3QueryException()
        error.id = "770"
        connection.clientmove.side_effect = error
        utils.multi_move(7, '!multimove "source" "target"')

    def test_setup_stores_bot_and_normalizes_dry_run_value(self):
        bot = SimpleNamespace(ts3conn=Mock())

        utils.setup(bot, enable_dry_run=True)

        self.assertIs(utils.BOT, bot)
        self.assertTrue(utils.DRY_RUN)


if __name__ == "__main__":
    unittest.main()
