"""Unit tests for the AFK mover plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import (
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.afk_mover import main as afk_mover


class AfkMoverTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = afk_mover.AfkMover.__new__(afk_mover.AfkMover)
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        return plugin

    @staticmethod
    def _query_error(error_id=None):
        error = TS3QueryException()
        if error_id is not None:
            error.id = str(error_id)
        error.message = "query failed"
        return error

    def test_constructor_initializes_channel_and_tracking_state(self):
        connection = Mock()
        with (
            patch.object(afk_mover.AfkMover, "get_channel_by_name", return_value="9"),
            patch.object(
                afk_mover.AfkMover, "update_channel_ids_list", return_value=[]
            ),
            patch.object(afk_mover.AfkMover, "update_servergroup_ids_list"),
        ):
            plugin = afk_mover.AfkMover(Mock(), connection)
        self.assertEqual(plugin.afk_channel, "9")
        self.assertEqual(plugin.client_channels, {})

    def test_channel_and_servergroup_exclusions_cover_defaults_and_errors(self):
        plugin = self._plugin()
        with patch.object(afk_mover, "CHANNELS_TO_EXCLUDE", None):
            self.assertEqual(plugin.update_channel_ids_list(), [])
        with patch.object(afk_mover, "CHANNELS_TO_EXCLUDE", "Ignore"):
            plugin.ts3conn.channellist.side_effect = self._query_error()
            self.assertEqual(plugin.update_channel_ids_list(), [])

        with patch.object(afk_mover, "SERVERGROUPS_TO_EXCLUDE", None):
            plugin.update_servergroup_ids_list()
        with (
            patch.object(afk_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
            patch.object(
                afk_mover, "get_servergroups", side_effect=self._query_error()
            ),
        ):
            plugin.update_servergroup_ids_list()

    def test_away_list_handles_empty_malformed_and_negative_cases(self):
        plugin = self._plugin()
        plugin.afk_channel = "9"
        plugin.afk_list = None
        self.assertEqual(plugin.get_away_list(), [])
        plugin.afk_list = [
            {"client_type": "1", "client_away": "1", "cid": "1"},
            {"client_type": "0", "cid": "1"},
            {"client_type": "0", "client_away": "0", "cid": "1"},
            {"client_type": "0", "client_away": "1"},
            {"client_type": "0", "client_away": "1", "cid": "9"},
            {
                "client_type": "0",
                "client_away": "1",
                "cid": "2",
                "client_database_id": "7",
            },
            {
                "client_type": "0",
                "client_away": "1",
                "cid": "2",
                "client_database_id": "8",
            },
        ]
        plugin.channel_ids_to_ignore = ["2"]
        plugin.servergroup_ids_to_ignore = ["4"]
        plugin.get_servergroups_by_client = Mock(side_effect=[["4"], []])
        with (
            patch.object(afk_mover, "CHANNELS_TO_EXCLUDE", "Ignore"),
            patch.object(afk_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
        ):
            self.assertEqual(plugin.get_away_list(), [])

    def test_fallback_action_handles_disabled_missing_and_failure_paths(self):
        plugin = self._plugin()
        plugin.client_channels = {"42": "1"}
        with patch.object(afk_mover, "FALLBACK_ACTION", None):
            plugin.fallback_action(42)
        plugin.get_channel_by_name = Mock(return_value=None)
        with patch.object(afk_mover, "FALLBACK_ACTION", "Lobby"):
            plugin.fallback_action(42)
        with patch.object(afk_mover, "FALLBACK_ACTION", "Lobby"):
            plugin.get_channel_by_name.return_value = "11"
            plugin.ts3conn.clientmove.side_effect = afk_mover.TS3Exception()
            plugin.fallback_action(42)
            plugin.ts3conn.clientmove.side_effect = None
            plugin.client_channels = {}
            plugin.fallback_action(42)
        plugin.logger.error.assert_called()

    def test_move_all_back_handles_missing_clients_and_query_errors(self):
        plugin = self._plugin()
        plugin.client_channels = {"42": "1"}
        plugin.afk_channel = "9"
        plugin.get_back_list = Mock(
            return_value=[{"clid": "7", "cid": "9"}, {"clid": "42", "cid": "9"}]
        )
        plugin.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        plugin.ts3conn._parse_resp_to_dict.return_value = {
            "channel_maxclients": "-1",
            "channel_flag_password": "0",
        }
        plugin.move_all_back()
        plugin.ts3conn.clientmove.assert_called_once_with(1, 42)

        plugin.get_back_list.return_value = [{"clid": "42", "cid": "9"}]
        plugin.ts3conn._send.side_effect = self._query_error(768)
        plugin.move_all_back()
        plugin.ts3conn.channellist.side_effect = self._query_error()
        plugin.move_all_back()

    def test_move_to_afk_supports_dry_run_empty_and_failed_moves(self):
        plugin = self._plugin()
        plugin.afk_channel = "9"
        plugin.client_channels = {}
        clients = [{"clid": "42", "cid": "3", "client_nickname": "Ada"}]
        with patch.object(afk_mover, "DRY_RUN", True):
            plugin.move_to_afk(clients)
        self.assertEqual(plugin.client_channels, {})
        plugin.move_to_afk(None)
        plugin.ts3conn.clientmove.side_effect = afk_mover.TS3Exception()
        plugin.move_to_afk(clients)
        self.assertEqual(plugin.client_channels, {})

    def test_auto_move_all_runs_success_and_exception_paths(self):
        plugin = self._plugin()
        plugin.afk_channel = None
        plugin.auto_move_all()
        plugin.afk_channel = "9"
        plugin.stopped = Mock()
        plugin.stopped.wait.side_effect = [False, True]
        plugin.update_afk_list = Mock()
        plugin.move_all_back = Mock()
        plugin.move_all_afk = Mock()
        with patch.object(afk_mover, "ENABLE_AUTO_MOVE_BACK", True):
            plugin.auto_move_all()
        plugin.update_afk_list.assert_called_once()
        plugin.move_all_back.assert_called_once()
        plugin.move_all_afk.assert_called_once()

        plugin.stopped.wait.side_effect = [False, True]
        plugin.update_afk_list.side_effect = RuntimeError("broken")
        plugin.auto_move_all()

    def test_event_setup_and_version_are_exercised(self):
        plugin = self._plugin()
        plugin.client_channels = {"42": "1"}
        with patch.object(afk_mover, "PLUGIN_INFO", plugin):
            afk_mover.client_left(SimpleNamespace(client_id=42))
        self.assertEqual(plugin.client_channels, {})
        bot = SimpleNamespace(ts3conn=Mock())
        with patch.object(afk_mover, "start_plugin") as start:
            afk_mover.setup(bot, auto_start=True, enable_dry_run=True, channel="AFK")
        start.assert_called_once_with()
        with patch.object(afk_mover.teamspeak_bot, "send_msg_to_client") as send:
            afk_mover.BOT = bot
            afk_mover.send_version(7)
        send.assert_called_once()

    def test_channel_lookup_returns_id_and_handles_missing_channel(self):
        plugin = self._plugin()
        plugin.ts3conn.channelfind.return_value = [{"cid": "42"}]
        self.assertEqual(plugin.get_channel_by_name("AFK"), "42")

        plugin.ts3conn.channelfind.return_value = []
        self.assertIsNone(plugin.get_channel_by_name("missing"))
        plugin.logger.warning.assert_called_once()

    def test_update_afk_list_handles_query_errors(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.side_effect = afk_mover.TS3Exception("down")

        plugin.update_afk_list()

        self.assertEqual(plugin.afk_list, [])

    def test_get_back_list_only_returns_clients_back_from_afk(self):
        plugin = self._plugin()
        plugin.afk_channel = "9"
        plugin.afk_list = [
            {"clid": "1", "cid": "9", "client_away": "0"},
            {"clid": "2", "cid": "9", "client_away": "1"},
            {"clid": "3", "cid": "8", "client_away": "0"},
        ]

        self.assertEqual(
            plugin.get_back_list(),
            [{"clid": "1", "cid": "9", "client_away": "0"}],
        )

    def test_move_all_back_uses_fallback_for_unexpected_channel_errors(self):
        plugin = self._plugin()
        plugin.client_channels = {"42": "1"}
        plugin.afk_channel = "9"
        plugin.afk_list = [{"clid": "42", "cid": "9", "client_away": "0"}]
        error = TS3QueryException()
        error.id = 999
        plugin.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        plugin.ts3conn._send.side_effect = error
        plugin.fallback_action = Mock()

        plugin.move_all_back()

        plugin.fallback_action.assert_called_once_with(42)

    def test_auto_move_stays_inactive_without_target_channel(self):
        plugin = self._plugin()
        plugin.afk_channel = None
        plugin.stopped = Mock()

        plugin.auto_move_all()

        plugin.stopped.wait.assert_not_called()
        plugin.logger.warning.assert_called_once()

    def test_channel_and_servergroup_exclusions_are_resolved(self):
        plugin = self._plugin()
        plugin.ts3conn.channellist.return_value = [
            {"cid": "1", "channel_name": "AFK"},
            {"cid": "2", "channel_name": "Ignore me"},
        ]

        with patch.object(afk_mover, "CHANNELS_TO_EXCLUDE", "Ignore"):
            self.assertEqual(plugin.update_channel_ids_list(), ["2"])

        with (
            patch.object(afk_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
            patch.object(
                afk_mover,
                "get_servergroups",
                return_value=[
                    {"type": "0", "sgid": "1", "name": "Support"},
                    {"type": "1", "sgid": "2", "name": "Support"},
                ],
            ),
        ):
            plugin.update_servergroup_ids_list()

        self.assertEqual(plugin.servergroup_ids_to_ignore, ["2"])

    def test_get_servergroups_by_client_returns_ids(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"sgid": "1"},
            {"sgid": "2"},
        ]

        self.assertEqual(plugin.get_servergroups_by_client("7"), ["1", "2"])
        plugin.ts3conn._send.assert_called_once_with(
            "servergroupsbyclientid", ["cldbid=7"]
        )

    def test_get_away_list_applies_all_exclusions(self):
        plugin = self._plugin()
        plugin.afk_channel = "9"
        plugin.channel_ids_to_ignore = ["3"]
        plugin.servergroup_ids_to_ignore = ["8"]
        plugin.afk_list = [
            {"client_type": "1", "client_away": "1", "cid": "1"},
            {"client_type": "0", "cid": "1"},
            {"client_type": "0", "client_away": "0", "cid": "1"},
            {"client_type": "0", "client_away": "1", "cid": "9"},
            {"client_type": "0", "client_away": "1"},
            {"client_type": "0", "client_away": "1", "cid": "3"},
            {
                "client_type": "0",
                "client_away": "1",
                "cid": "1",
                "client_database_id": "7",
            },
            {
                "client_type": "0",
                "client_away": "1",
                "cid": "1",
                "client_database_id": "6",
            },
        ]
        plugin.get_servergroups_by_client = Mock(side_effect=[["8"], []])

        with (
            patch.object(afk_mover, "CHANNELS_TO_EXCLUDE", "Ignore"),
            patch.object(afk_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
        ):
            self.assertEqual(
                plugin.get_away_list(),
                [plugin.afk_list[-1]],
            )

    def test_move_to_afk_records_successful_moves(self):
        plugin = self._plugin()
        plugin.client_channels = {}
        plugin.afk_channel = "9"
        clients = [{"clid": "42", "cid": "3", "client_nickname": "Ada"}]

        with patch.object(afk_mover, "DRY_RUN", False):
            plugin.move_to_afk(clients)

        plugin.ts3conn.clientmove.assert_called_once_with("9", 42)
        self.assertEqual(plugin.client_channels, {"42": "3"})

    def test_fallback_action_moves_client_and_forgets_original_channel(self):
        plugin = self._plugin()
        plugin.client_channels = {"42": "3"}
        plugin.get_channel_by_name = Mock(return_value="11")

        with patch.object(afk_mover, "FALLBACK_ACTION", "Lobby"):
            plugin.fallback_action(42)

        plugin.ts3conn.clientmove.assert_called_once_with("11", 42)
        self.assertEqual(plugin.client_channels, {})

    def test_move_all_back_moves_client_when_previous_channel_is_available(self):
        plugin = self._plugin()
        plugin.client_channels = {"42": "1"}
        plugin.afk_channel = "9"
        plugin.afk_list = [{"clid": "42", "cid": "9", "client_away": "0"}]
        plugin.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        plugin.ts3conn._parse_resp_to_dict.return_value = {
            "channel_maxclients": "2",
            "channel_flag_password": "0",
        }

        with patch.object(afk_mover, "RESP_CHANNEL_SETTINGS", True):
            plugin.move_all_back()

        plugin.ts3conn.clientmove.assert_called_once_with(1, 42)
        self.assertEqual(plugin.client_channels, {})


if __name__ == "__main__":
    unittest.main()
