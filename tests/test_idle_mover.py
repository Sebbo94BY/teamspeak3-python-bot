"""Unit tests for the idle mover plugin."""

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

from modules.idle_mover import main as idle_mover


class IdleMoverTests(unittest.TestCase):  # pylint: disable=too-many-public-methods
    @staticmethod
    def _plugin():
        plugin = idle_mover.IdleMover.__new__(idle_mover.IdleMover)
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        plugin.afk_channel = "9"
        plugin.channel_ids_to_ignore = []
        plugin.servergroup_ids_to_ignore = []
        plugin.channel_configs = []
        plugin.idling_clients = {}
        return plugin

    @staticmethod
    def _query_error(error_id=None):
        error = TS3QueryException()
        if error_id is not None:
            error.id = str(error_id)
        error.message = "query failed"
        return error

    def test_constructor_initializes_configuration_and_handles_empty_settings(self):
        connection = Mock()
        with (
            patch.object(idle_mover, "CHANNEL_NAME", "AFK"),
            patch.object(idle_mover, "CHANNELS_TO_EXCLUDE", None),
            patch.object(idle_mover, "SERVERGROUPS_TO_EXCLUDE", None),
            patch.object(idle_mover.IdleMover, "get_channel_by_name", return_value="9"),
            patch.object(
                idle_mover.IdleMover, "update_channel_ids_list", return_value=[]
            ),
            patch.object(
                idle_mover.IdleMover, "update_servergroup_ids_list", return_value=[]
            ),
            patch.object(
                idle_mover.IdleMover, "parse_channel_settings", return_value=[]
            ),
        ):
            plugin = idle_mover.IdleMover(Mock(), connection)
        self.assertEqual(plugin.afk_channel, "9")
        self.assertEqual(plugin.idling_clients, {})

    def test_update_client_list_filters_serverquery_and_propagates_errors(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "clid": "1"},
            {"client_type": "1", "clid": "2"},
        ]
        self.assertEqual(
            plugin.update_client_list(), [plugin.ts3conn.clientlist.return_value[0]]
        )
        plugin.ts3conn.clientlist.side_effect = idle_mover.TS3Exception()
        with self.assertRaises(idle_mover.TS3Exception):
            plugin.update_client_list()

    def test_exclusion_lookups_cover_defaults_and_query_failures(self):
        plugin = self._plugin()
        with patch.object(idle_mover, "CHANNELS_TO_EXCLUDE", None):
            self.assertEqual(plugin.update_channel_ids_list(), [])
        with (
            patch.object(idle_mover, "CHANNELS_TO_EXCLUDE", "Ignore"),
            patch.object(idle_mover, "TS3QueryException", TS3QueryException),
        ):
            plugin.ts3conn.channellist.side_effect = self._query_error()
            self.assertEqual(plugin.update_channel_ids_list(), [])

        with patch.object(idle_mover, "SERVERGROUPS_TO_EXCLUDE", None):
            self.assertEqual(plugin.update_servergroup_ids_list(), [])
        with (
            patch.object(idle_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
            patch.object(
                idle_mover, "get_servergroups", side_effect=self._query_error()
            ),
        ):
            self.assertEqual(plugin.update_servergroup_ids_list(), [])

    def test_parse_settings_handles_empty_multiple_aliases_and_malformed_keys(self):
        plugin = self._plugin()
        plugin.get_channel_by_name = Mock(side_effect=["1", "2"])
        self.assertEqual(plugin.parse_channel_settings({}), [])
        self.assertEqual(
            plugin.parse_channel_settings(
                {
                    "first.channel_name": "First",
                    "first.min_idle_time_seconds": "10",
                    "second.channel_name": "Second",
                    "second.min_idle_time_seconds": "20",
                }
            ),
            [
                {
                    "channel_name": "First",
                    "channel_id": "1",
                    "min_idle_time_seconds": "10",
                },
                {
                    "channel_name": "Second",
                    "channel_id": "2",
                    "min_idle_time_seconds": "20",
                },
            ],
        )
        with self.assertRaises(ValueError):
            plugin.parse_channel_settings({"malformed": "value"})

    def test_idle_list_covers_missing_fields_channel_settings_and_exclusions(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0"},
            {"client_type": "0", "cid": "9", "client_idle_time": "900000"},
            {
                "client_type": "0",
                "cid": "2",
                "client_idle_time": "900000",
                "client_database_id": "1",
            },
            {
                "client_type": "0",
                "cid": "3",
                "client_idle_time": "900000",
                "client_database_id": "2",
            },
            {
                "client_type": "0",
                "cid": "4",
                "client_idle_time": "10000",
                "client_database_id": "3",
            },
        ]
        plugin.channel_ids_to_ignore = ["2"]
        plugin.servergroup_ids_to_ignore = ["8"]
        plugin.get_servergroups_by_client = Mock(side_effect=[["8"], []])
        plugin.channel_configs = [{"channel_id": "4", "min_idle_time_seconds": "20"}]
        with (
            patch.object(idle_mover, "CHANNELS_TO_EXCLUDE", "Ignore"),
            patch.object(idle_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
            patch.object(idle_mover, "IDLE_TIME_SECONDS", 600),
        ):
            self.assertEqual(plugin.get_idle_list(), [])

        plugin.ts3conn.clientlist.return_value = [
            {
                "client_type": "0",
                "cid": "4",
                "client_idle_time": "20000",
                "client_database_id": "3",
            }
        ]
        plugin.get_servergroups_by_client.return_value = []
        with (
            patch.object(idle_mover, "CHANNELS_TO_EXCLUDE", None),
            patch.object(idle_mover, "SERVERGROUPS_TO_EXCLUDE", None),
        ):
            self.assertEqual(len(plugin.get_idle_list()), 1)

    def test_back_list_handles_empty_missing_moved_and_query_error_clients(self):
        plugin = self._plugin()
        self.assertEqual(plugin.get_back_list(), {})
        plugin.idling_clients = {42: 1, 43: 1, 44: 1}
        plugin.ts3conn.clientinfo.side_effect = [
            {},
            {
                "client_idle_time": "1000",
                "cid": "8",
                "client_database_id": "43",
                "client_nickname": "Lin",
            },
            {
                "client_idle_time": "900000",
                "cid": "9",
                "client_database_id": "44",
                "client_nickname": "Max",
            },
        ]
        with patch.object(idle_mover, "IDLE_TIME_SECONDS", 600):
            self.assertEqual(plugin.get_back_list(), {})
        self.assertNotIn(43, plugin.idling_clients)
        self.assertIn(44, plugin.idling_clients)

        plugin.idling_clients = {45: 1}
        plugin.ts3conn.clientinfo.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.get_back_list()

    def test_channel_lookup_and_move_paths_cover_negative_cases(self):
        plugin = self._plugin()
        plugin.ts3conn.channelfind.side_effect = idle_mover.TS3Exception()
        self.assertIsNone(plugin.get_channel_by_name("AFK"))
        plugin.ts3conn.channelfind.side_effect = [[]]
        self.assertIsNone(plugin.get_channel_by_name("AFK"))

        plugin.get_idle_list = Mock(return_value=[])
        plugin.move_to_afk()
        plugin.move_to_afk = Mock(side_effect=AttributeError("connection"))
        idle_mover.IdleMover.move_all_afk(plugin)

        plugin.idling_clients = {42: 1}
        plugin.get_channel_by_name = Mock(return_value=None)
        with patch.object(idle_mover, "FALLBACK_ACTION", "Lobby"):
            plugin.fallback_action(42)
        with patch.object(idle_mover, "FALLBACK_ACTION", "Lobby"):
            plugin.get_channel_by_name.return_value = "11"
            plugin.ts3conn.clientmove.side_effect = idle_mover.TS3Exception()
            plugin.fallback_action(42)
            plugin.ts3conn.clientmove.side_effect = None
            plugin.idling_clients = {}
            plugin.fallback_action(42)
        plugin.logger.error.assert_called()

    def test_move_back_uses_fallback_for_capacity_password_and_invalid_channel(self):
        plugin = self._plugin()
        plugin.idling_clients = {42: 1}
        plugin.get_back_list = Mock(return_value={42: 1})
        plugin.fallback_action = Mock()
        plugin.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "1"}]
        plugin.ts3conn._parse_resp_to_dict.return_value = {
            "channel_maxclients": "1",
            "channel_flag_password": "0",
        }
        with patch.object(idle_mover, "RESP_CHANNEL_SETTINGS", True):
            plugin.move_all_back()
        plugin.fallback_action.assert_called_once_with(42)

        plugin.fallback_action.reset_mock()
        plugin.ts3conn._parse_resp_to_dict.return_value = {
            "channel_maxclients": "2",
            "channel_flag_password": "1",
        }
        plugin.move_all_back()
        plugin.fallback_action.assert_called_once_with(42)

        plugin.ts3conn._send.side_effect = self._query_error(768)
        plugin.move_all_back()
        plugin.ts3conn.channellist.side_effect = self._query_error()
        plugin.move_all_back()

    def test_move_back_handles_all_clientmove_error_codes(self):
        for error_id in (768, 777, 781, 999):
            plugin = self._plugin()
            plugin.idling_clients = {42: 1}
            plugin.get_back_list = Mock(return_value={42: 1})
            plugin.fallback_action = Mock()
            plugin.ts3conn.channellist.return_value = [
                {"cid": "1", "total_clients": "0"}
            ]
            plugin.ts3conn._parse_resp_to_dict.return_value = {
                "channel_maxclients": "-1",
                "channel_flag_password": "0",
            }
            plugin.ts3conn.clientmove.side_effect = self._query_error(error_id)
            plugin.move_all_back()
            plugin.fallback_action.assert_called_once_with(42)

    def test_auto_move_all_runs_positive_and_error_paths(self):
        plugin = self._plugin()
        plugin.stopped = Mock()
        plugin.stopped.wait.side_effect = [False, True]
        plugin.move_all_back = Mock()
        plugin.move_all_afk = Mock()
        with patch.object(idle_mover, "ENABLE_AUTO_MOVE_BACK", True):
            plugin.auto_move_all()
        plugin.move_all_back.assert_called_once()
        plugin.move_all_afk.assert_called_once()

        plugin.stopped.wait.side_effect = [False, True]
        plugin.move_all_afk.side_effect = RuntimeError("broken")
        plugin.auto_move_all()

    def test_event_setup_and_version_are_exercised(self):
        plugin = self._plugin()
        plugin.idling_clients = {42: 1}
        with patch.object(idle_mover, "PLUGIN_INFO", plugin):
            idle_mover.client_left(SimpleNamespace(client_id="42"))
        self.assertEqual(plugin.idling_clients, {})
        bot = SimpleNamespace(ts3conn=Mock())
        with patch.object(idle_mover, "start_plugin") as start:
            idle_mover.setup(bot, auto_start=True, enable_dry_run=True, channel="Idle")
        start.assert_called_once_with()
        with patch.object(idle_mover.teamspeak_bot, "send_msg_to_client") as send:
            idle_mover.BOT = bot
            idle_mover.send_version(7)
        send.assert_called_once()

    def test_update_client_list_ignores_serverquery_clients(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "clid": "1"},
            {"client_type": "1", "clid": "2"},
        ]

        self.assertEqual(
            plugin.update_client_list(), [{"client_type": "0", "clid": "1"}]
        )

    def test_parse_channel_settings_resolves_channel_and_validates_fields(self):
        plugin = self._plugin()
        plugin.get_channel_by_name = Mock(return_value="42")

        self.assertEqual(
            plugin.parse_channel_settings(
                {
                    "support.channel_name": "Support",
                    "support.min_idle_time_seconds": "300",
                }
            ),
            [
                {
                    "channel_name": "Support",
                    "channel_id": "42",
                    "min_idle_time_seconds": "300",
                }
            ],
        )

    def test_parse_channel_settings_rejects_incomplete_configuration(self):
        plugin = self._plugin()
        plugin.get_channel_by_name = Mock(return_value="42")

        with self.assertRaisesRegex(ValueError, "Both options must be defined"):
            plugin.parse_channel_settings({"support.channel_name": "Support"})

    def test_get_idle_list_applies_default_threshold_and_exclusions(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {
                "client_type": "0",
                "cid": "1",
                "client_idle_time": "601000",
                "clid": "1",
            },
            {
                "client_type": "0",
                "cid": "1",
                "client_idle_time": "599000",
                "clid": "2",
            },
            {
                "client_type": "0",
                "cid": "9",
                "client_idle_time": "900000",
                "clid": "3",
            },
        ]

        self.assertEqual(
            plugin.get_idle_list(), [plugin.ts3conn.clientlist.return_value[0]]
        )

    def test_move_all_back_uses_fallback_for_unexpected_channel_errors(self):
        plugin = self._plugin()
        plugin.idling_clients = {42: 1}
        plugin.get_back_list = Mock(return_value={42: 1})
        plugin.fallback_action = Mock()
        error = TS3QueryException()
        error.id = 999
        plugin.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        plugin.ts3conn._send.side_effect = error

        plugin.move_all_back()

        plugin.fallback_action.assert_called_once_with(42)

    def test_failed_move_is_not_recorded_as_an_idle_client(self):
        plugin = self._plugin()
        plugin.get_idle_list = Mock(return_value=[{"clid": "42", "cid": "1"}])
        plugin.ts3conn.clientmove.side_effect = idle_mover.TS3Exception("down")

        plugin.move_to_afk()

        self.assertEqual(plugin.idling_clients, {})

    def test_channel_and_servergroup_exclusions_are_resolved(self):
        plugin = self._plugin()
        plugin.ts3conn.channellist.return_value = [
            {"cid": "1", "channel_name": "AFK"},
            {"cid": "2", "channel_name": "Ignore"},
        ]

        with patch.object(idle_mover, "CHANNELS_TO_EXCLUDE", "Ignore"):
            self.assertEqual(plugin.update_channel_ids_list(), ["2"])

        with (
            patch.object(idle_mover, "SERVERGROUPS_TO_EXCLUDE", "Support"),
            patch.object(
                idle_mover,
                "get_servergroups",
                return_value=[
                    {"type": "0", "sgid": "1", "name": "Support"},
                    {"type": "1", "sgid": "2", "name": "Support"},
                ],
            ),
        ):
            self.assertEqual(plugin.update_servergroup_ids_list(), ["2"])

    def test_get_servergroups_by_client_returns_ids(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [{"sgid": "4"}]

        self.assertEqual(plugin.get_servergroups_by_client("7"), ["4"])
        plugin.ts3conn._send.assert_called_once_with(
            "servergroupsbyclientid", ["cldbid=7"]
        )

    def test_get_back_list_returns_clients_below_threshold(self):
        plugin = self._plugin()
        plugin.idling_clients = {42: 1, 43: 1}
        plugin.ts3conn.clientinfo.side_effect = [
            {
                "client_idle_time": "1000",
                "cid": "9",
                "client_database_id": "42",
                "client_nickname": "Ada",
            },
            {
                "client_idle_time": "1000",
                "cid": "8",
                "client_database_id": "43",
                "client_nickname": "Lin",
            },
        ]

        with patch.object(idle_mover, "IDLE_TIME_SECONDS", 600):
            self.assertEqual(plugin.get_back_list(), {42: 1})

        self.assertNotIn(43, plugin.idling_clients)

    def test_move_to_afk_records_successful_moves(self):
        plugin = self._plugin()
        plugin.afk_channel = "9"
        plugin.get_idle_list = Mock(
            return_value=[{"clid": "42", "cid": "3", "client_nickname": "Ada"}]
        )

        with patch.object(idle_mover, "DRY_RUN", False):
            plugin.move_to_afk()

        plugin.ts3conn.clientmove.assert_called_once_with("9", 42)
        self.assertEqual(plugin.idling_clients, {42: 3})

    def test_move_all_back_moves_client_when_previous_channel_is_available(self):
        plugin = self._plugin()
        plugin.idling_clients = {42: 1}
        plugin.get_back_list = Mock(return_value={42: 1})
        plugin.ts3conn.channellist.return_value = [{"cid": "1", "total_clients": "0"}]
        plugin.ts3conn._parse_resp_to_dict.return_value = {
            "channel_maxclients": "2",
            "channel_flag_password": "0",
        }

        with patch.object(idle_mover, "RESP_CHANNEL_SETTINGS", True):
            plugin.move_all_back()

        plugin.ts3conn.clientmove.assert_called_once_with(1, 42)
        self.assertEqual(plugin.idling_clients, {})


if __name__ == "__main__":
    unittest.main()
