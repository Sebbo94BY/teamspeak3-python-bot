"""Unit tests for the inactive-client kicker plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from unittest.mock import Mock, patch

from tests.support import (
    TS3Exception,
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.kick_inactive_clients import main as kick_inactive


class KickInactiveClientsTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = kick_inactive.KickInactiveClients.__new__(
            kick_inactive.KickInactiveClients
        )
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        plugin.servergroup_ids_to_ignore = []
        return plugin

    @staticmethod
    def _query_error():
        return TS3QueryException()

    def test_serverinfo_and_client_list_failures_reset_state(self):
        plugin = self._plugin()
        plugin.ts3conn.serverinfo.side_effect = TS3Exception()
        plugin.get_serverinfo()
        self.assertEqual(plugin.serverinfo, [])

        plugin.ts3conn.clientlist.side_effect = TS3Exception()
        plugin.update_client_list()
        self.assertEqual(plugin.idle_list, [])

    def test_servergroup_exclusions_are_resolved_and_query_errors_ignored(self):
        plugin = self._plugin()
        with (
            patch.object(kick_inactive, "SERVERGROUPS_TO_EXCLUDE", "VIP"),
            patch.object(
                kick_inactive,
                "get_servergroups",
                return_value=[
                    {"type": "0", "sgid": "1", "name": "VIP"},
                    {"type": "1", "sgid": "2", "name": "VIP"},
                ],
            ),
        ):
            plugin.update_servergroup_ids_list()
        self.assertEqual(plugin.servergroup_ids_to_ignore, ["2"])

        with (
            patch.object(kick_inactive, "SERVERGROUPS_TO_EXCLUDE", "VIP"),
            patch.object(
                kick_inactive, "get_servergroups", side_effect=self._query_error()
            ),
        ):
            plugin.update_servergroup_ids_list()

    def test_servergroup_membership_lookup_handles_success_and_errors(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"sgid": "2"},
            {"sgid": "3"},
        ]
        self.assertEqual(plugin.get_servergroups_by_client("7"), ["2", "3"])
        plugin.ts3conn._send.side_effect = self._query_error()
        self.assertEqual(plugin.get_servergroups_by_client("7"), [])

    def test_idle_list_filters_malformed_serverquery_excluded_and_recent_clients(self):
        plugin = self._plugin()
        plugin.idle_list = [
            {},
            {"cid": "1"},
            {"cid": "1", "client_idle_time": "900000", "client_type": "1"},
            {
                "cid": "1",
                "client_idle_time": "900000",
                "client_type": "0",
                "client_database_id": "7",
            },
            {
                "cid": "1",
                "client_idle_time": "1000",
                "client_type": "0",
                "client_database_id": "8",
            },
        ]
        with (
            patch.object(kick_inactive, "SERVERGROUPS_TO_EXCLUDE", "VIP"),
            patch.object(kick_inactive, "IDLE_TIME_SECONDS", 600),
        ):
            plugin.servergroup_ids_to_ignore = ["2"]
            plugin.get_servergroups_by_client = Mock(side_effect=[["2"], []])
            result = plugin.get_idle_list()
        self.assertEqual(result, [])

        plugin.idle_list = None
        self.assertEqual(plugin.get_idle_list(), [])

    def test_idle_list_keeps_old_clients_not_in_excluded_groups(self):
        plugin = self._plugin()
        plugin.idle_list = [
            {
                "cid": "1",
                "client_idle_time": "900000",
                "client_type": "0",
                "client_database_id": "8",
            }
        ]
        with (
            patch.object(kick_inactive, "SERVERGROUPS_TO_EXCLUDE", "VIP"),
            patch.object(kick_inactive, "IDLE_TIME_SECONDS", 600),
        ):
            plugin.servergroup_ids_to_ignore = ["2"]
            plugin.get_servergroups_by_client = Mock(return_value=[])
            self.assertEqual(plugin.get_idle_list(), plugin.idle_list)

    def test_kick_all_idle_clients_supports_dry_run_and_query_errors(self):
        plugin = self._plugin()
        plugin.serverinfo = {
            "virtualserver_clientsonline": "10",
            "virtualserver_maxclients": "10",
        }
        plugin.get_idle_list = Mock(return_value=[{"clid": "42"}])
        with (
            patch.object(kick_inactive, "DRY_RUN", True),
            patch.object(kick_inactive, "CLIENTSONLINE_KICK_THRESHOLD", 10),
        ):
            plugin.kick_all_idle_clients()
        plugin.ts3conn.clientkick.assert_not_called()

        with (
            patch.object(kick_inactive, "DRY_RUN", False),
            patch.object(kick_inactive, "CLIENTSONLINE_KICK_THRESHOLD", 10),
        ):
            plugin.ts3conn.clientkick.side_effect = TS3Exception()
            plugin.kick_all_idle_clients()
        plugin.ts3conn.clientkick.assert_called()

        plugin.get_idle_list.return_value = None
        plugin.kick_all_idle_clients()

    def test_loop_runs_once_and_clears_channel_cache(self):
        plugin = self._plugin()
        plugin.client_channels = {"1": "2"}
        plugin.stopped = Mock()
        plugin.stopped.wait.side_effect = [False, True]
        plugin.get_serverinfo = Mock()
        plugin.update_client_list = Mock()
        plugin.kick_all_idle_clients = Mock()
        plugin.loop_until_stopped()
        plugin.get_serverinfo.assert_called_once()
        plugin.update_client_list.assert_called_once()
        plugin.kick_all_idle_clients.assert_called_once()
        self.assertEqual(plugin.client_channels, {})

        plugin.stopped.wait.side_effect = [False, True]
        plugin.get_serverinfo.side_effect = RuntimeError("broken")
        plugin.loop_until_stopped()

    def test_setup_and_lifecycle_functions(self):
        bot = type("Bot", (), {"ts3conn": Mock()})()
        with patch.object(kick_inactive, "start_plugin") as start:
            kick_inactive.setup(bot, auto_start=True, enable_dry_run=True, frequency=3)
        start.assert_called_once_with()
        self.assertTrue(kick_inactive.DRY_RUN)

        with patch.object(kick_inactive.teamspeak_bot, "send_msg_to_client") as send:
            kick_inactive.BOT = bot
            kick_inactive.send_version(7)
        send.assert_called_once()

    def test_update_client_list_ignores_serverquery_clients(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "clid": "1"},
            {"client_type": "1", "clid": "2"},
        ]

        plugin.update_client_list()

        self.assertEqual(plugin.idle_list, [{"client_type": "0", "clid": "1"}])

    def test_get_idle_list_applies_idle_threshold(self):
        plugin = self._plugin()
        plugin.idle_list = [
            {
                "client_type": "0",
                "cid": "1",
                "client_idle_time": "601000",
                "clid": "1",
            },
            {
                "client_type": "0",
                "cid": "1",
                "client_idle_time": "600000",
                "clid": "2",
            },
        ]

        with patch.object(kick_inactive, "IDLE_TIME_SECONDS", 600):
            self.assertEqual(plugin.get_idle_list(), [plugin.idle_list[0]])

    def test_kick_all_idle_clients_respects_server_capacity_threshold(self):
        plugin = self._plugin()
        plugin.serverinfo = {
            "virtualserver_clientsonline": "1",
            "virtualserver_maxclients": "10",
        }
        plugin.get_idle_list = Mock(return_value=[{"clid": "42"}])

        with patch.object(kick_inactive, "CLIENTSONLINE_KICK_THRESHOLD", 10):
            plugin.kick_all_idle_clients()

        plugin.ts3conn.clientkick.assert_not_called()

    def test_kick_all_idle_clients_kicks_clients_above_threshold(self):
        plugin = self._plugin()
        plugin.serverinfo = {
            "virtualserver_clientsonline": "10",
            "virtualserver_maxclients": "10",
        }
        plugin.get_idle_list = Mock(return_value=[{"clid": "42"}])

        with patch.object(kick_inactive, "CLIENTSONLINE_KICK_THRESHOLD", 10):
            plugin.kick_all_idle_clients()

        plugin.ts3conn.clientkick.assert_called_once_with(
            42, 5, kick_inactive.KICK_REASON_MESSAGE
        )


if __name__ == "__main__":
    unittest.main()
