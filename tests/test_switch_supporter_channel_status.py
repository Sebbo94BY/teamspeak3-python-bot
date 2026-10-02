"""Unit tests for the supporter-channel status plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from unittest.mock import Mock, call, patch

from tests.support import (
    TS3Exception,
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.switch_supporter_channel_status import main as supporter_status


class SwitchSupporterChannelStatusTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = supporter_status.SwitchSupporterChannelStatus.__new__(
            supporter_status.SwitchSupporterChannelStatus
        )
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        plugin.afk_channel_ids = []
        plugin.available_supporter_clients = []
        return plugin

    @staticmethod
    def _query_error(error_id=None):
        error = TS3QueryException()
        if error_id is not None:
            error.id = str(error_id)
        return error

    def test_channel_lookup_handles_errors_and_missing_matches(self):
        plugin = self._plugin()
        plugin.ts3conn.channelfind.side_effect = TS3Exception()
        self.assertIsNone(plugin.get_channel_by_name("Support"))
        plugin.ts3conn.channelfind.side_effect = [[]]
        self.assertIsNone(plugin.get_channel_by_name("Support"))

    def test_servergroup_lookup_requires_configuration_and_handles_query_errors(self):
        plugin = self._plugin()
        with patch.object(supporter_status, "SERVERGROUPS_TO_CHECK", None):
            with self.assertRaises(ValueError):
                plugin.update_servergroup_ids_to_check()

        with (
            patch.object(supporter_status, "SERVERGROUPS_TO_CHECK", "Supporters"),
            patch.object(
                supporter_status, "get_servergroups", side_effect=TS3QueryException()
            ),
        ):
            self.assertEqual(plugin.update_servergroup_ids_to_check(), [])

    def test_member_list_combines_groups_and_propagates_errors(self):
        plugin = self._plugin()
        plugin.servergroup_ids_to_check = ["1", "2"]
        plugin.ts3conn._parse_resp_to_list_of_dicts.side_effect = [
            [{"cldbid": "7"}],
            [{"cldbid": "8"}],
        ]
        self.assertEqual(plugin.update_servergroup_member_list(), ["7", "8"])

        plugin.ts3conn._send.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.update_servergroup_member_list()

    def test_check_all_connected_clients_updates_and_opens_channel(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [{"clid": "7"}, {"clid": "8"}]
        plugin.update_available_supporter_client_list = Mock()
        plugin.open_or_close_supporter_channel = Mock()
        plugin.check_all_connected_clients()
        self.assertEqual(
            plugin.update_available_supporter_client_list.call_args_list,
            [call("7"), call("8")],
        )
        plugin.open_or_close_supporter_channel.assert_called_once_with()

        plugin.ts3conn.clientlist.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.check_all_connected_clients()

    def test_available_supporter_list_handles_serverquery_nonmembers_and_afk(self):
        plugin = self._plugin()
        plugin.client_database_ids_to_check = ["7"]
        plugin.afk_channel_ids = [9]
        plugin.available_supporter_clients = [42]
        plugin.ts3conn.clientinfo.side_effect = [
            {"client_type": "1", "client_database_id": "7"},
            {"client_type": "0", "client_database_id": "8", "cid": "1"},
            {"client_type": "0", "client_database_id": "7", "cid": "9"},
        ]
        plugin.update_available_supporter_client_list(42)
        plugin.update_available_supporter_client_list(42)
        plugin.update_available_supporter_client_list(42)
        self.assertEqual(plugin.available_supporter_clients, [])

        plugin.ts3conn.clientinfo.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.update_available_supporter_client_list(42)

    def test_close_channel_and_skip_already_matching_status(self):
        plugin = self._plugin()
        plugin.supporter_channel_id = "5"
        plugin.available_supporter_clients = []
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"channel_name": "Support [OPEN]"}
        ]
        with patch.object(supporter_status, "MINIMUM_ONLINE_CLIENTS", 1):
            plugin.open_or_close_supporter_channel()
        self.assertEqual(plugin.ts3conn._send.call_count, 2)

        plugin.ts3conn._send.reset_mock()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"channel_name": "Support [CLOSED]"}
        ]
        plugin.open_or_close_supporter_channel()
        plugin.ts3conn._send.assert_called_once_with("channelinfo", ["cid=5"])

    def test_dry_run_and_channel_edit_errors_are_safe(self):
        plugin = self._plugin()
        plugin.supporter_channel_id = "5"
        plugin.available_supporter_clients = [42]
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"channel_name": "Support"}
        ]
        with patch.object(supporter_status, "DRY_RUN", True):
            plugin.open_or_close_supporter_channel()
        self.assertEqual(plugin.ts3conn._send.call_count, 1)

        plugin.ts3conn._send.reset_mock()
        plugin.ts3conn._send.side_effect = [None, self._query_error(771)]
        plugin.open_or_close_supporter_channel()
        plugin.ts3conn._send.reset_mock()
        plugin.ts3conn._send.side_effect = [None, self._query_error(999)]
        with self.assertRaises(TS3QueryException):
            plugin.open_or_close_supporter_channel()

        plugin.ts3conn._send.side_effect = [None, TS3Exception()]
        with self.assertRaises(TS3Exception):
            plugin.open_or_close_supporter_channel()

    def test_event_handlers_update_lists_and_status(self):
        plugin = self._plugin()
        plugin.afk_channel_ids = [9]
        plugin.afk_clients = []
        plugin.available_supporter_clients = [42]
        plugin.ts3conn.clientinfo.return_value = {
            "client_type": "0",
            "client_database_id": "7",
            "cid": "9",
        }
        with (
            patch.object(supporter_status, "PLUGIN_INFO", plugin),
            patch.object(
                supporter_status.SwitchSupporterChannelStatus,
                "update_available_supporter_client_list",
            ) as update,
            patch.object(
                supporter_status.SwitchSupporterChannelStatus,
                "open_or_close_supporter_channel",
            ) as update_status,
        ):
            supporter_status.client_joined_server(
                type("Event", (), {"target_channel_id": "9", "client_id": "42"})()
            )
            supporter_status.client_moved_channel(
                type("Event", (), {"target_channel_id": "10", "client_id": "42"})()
            )
            supporter_status.client_left_server(
                type("Event", (), {"client_id": "42"})()
            )
        self.assertEqual(plugin.afk_clients, [])
        self.assertEqual(update.call_count, 2)
        self.assertEqual(update_status.call_count, 3)

    def test_setup_and_version_delegate(self):
        bot = type("Bot", (), {"ts3conn": Mock()})()
        with patch.object(supporter_status, "start_plugin") as start:
            supporter_status.setup(
                bot,
                auto_start=True,
                enable_dry_run=True,
                supporter_channel_name="Support",
                servergroups_to_check="Supporters",
                minimum_online_clients=2,
                afk_channel_names="AFK",
            )
        start.assert_called_once_with()
        self.assertTrue(supporter_status.DRY_RUN)
        with patch.object(supporter_status.teamspeak_bot, "send_msg_to_client") as send:
            supporter_status.BOT = bot
            supporter_status.send_version(7)
        send.assert_called_once()

    def test_servergroup_lookup_ignores_templates(self):
        plugin = self._plugin()
        with (
            patch.object(
                supporter_status,
                "SERVERGROUPS_TO_CHECK",
                "Supporters,Moderators",
            ),
            patch.object(
                supporter_status,
                "get_servergroups",
                return_value=[
                    {"type": "0", "sgid": "1", "name": "Supporters"},
                    {"type": "1", "sgid": "2", "name": "Supporters"},
                    {"type": "1", "sgid": "3", "name": "Other"},
                ],
            ),
        ):
            self.assertEqual(plugin.update_servergroup_ids_to_check(), ["2"])

    def test_available_supporter_list_excludes_serverquery_and_afk_clients(self):
        plugin = self._plugin()
        plugin.client_database_ids_to_check = ["7"]
        plugin.afk_channel_ids = [9]
        plugin.ts3conn.clientinfo.side_effect = [
            {"client_type": "0", "client_database_id": "7", "cid": "9"},
            {"client_type": "1", "client_database_id": "7", "cid": "1"},
        ]

        plugin.update_available_supporter_client_list(42)
        plugin.update_available_supporter_client_list(43)

        self.assertEqual(plugin.available_supporter_clients, [])

    def test_open_channel_sets_unlimited_capacity_and_open_suffix(self):
        plugin = self._plugin()
        plugin.supporter_channel_id = "5"
        plugin.available_supporter_clients = [42]
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"channel_name": "Support [CLOSED]"}
        ]

        with patch.object(supporter_status, "MINIMUM_ONLINE_CLIENTS", 1):
            plugin.open_or_close_supporter_channel()

        self.assertEqual(
            plugin.ts3conn._send.call_args_list[-1],
            call(
                "channeledit",
                ["cid=5", "channel_maxclients=-1", "channel_name=Support [OPEN]"],
            ),
        )

    def test_missing_supporter_channel_is_inactive(self):
        plugin = self._plugin()
        plugin.supporter_channel_id = None

        plugin.open_or_close_supporter_channel()

        plugin.ts3conn._send.assert_not_called()
        plugin.logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
