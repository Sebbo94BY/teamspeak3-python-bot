"""Unit tests for the channel requester plugin."""

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

from modules.channel_requester import main as channel_requester


class ChannelRequesterTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = channel_requester.ChannelRequester.__new__(
            channel_requester.ChannelRequester
        )
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        return plugin

    @staticmethod
    def _query_error(error_id=None):
        error = TS3QueryException()
        if error_id is not None:
            error.id = str(error_id)
        return error

    def test_channel_group_lookup_handles_no_match_and_sdk_error(self):
        requester = self._plugin()
        requester.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"type": "1", "name": "Other", "cgid": "3"}
        ]
        self.assertIsNone(requester.get_channel_group_by_name("Admin"))
        requester.ts3conn._send.side_effect = TS3Exception()
        with self.assertRaises(TS3Exception):
            requester.get_channel_group_by_name("Admin")

    def test_servergroup_exclusions_cover_defaults_and_query_errors(self):
        requester = self._plugin()
        with patch.object(channel_requester, "SERVERGROUPS_TO_EXCLUDE", None):
            self.assertEqual(requester.update_servergroup_ids_list(), [])
        with (
            patch.object(channel_requester, "SERVERGROUPS_TO_EXCLUDE", "Support"),
            patch.object(
                channel_requester, "get_servergroups", side_effect=TS3QueryException()
            ),
        ):
            self.assertEqual(requester.update_servergroup_ids_list(), [])

    def test_channel_lookup_and_parser_handle_errors_and_aliases(self):
        requester = self._plugin()
        requester.ts3conn.channelfind.side_effect = TS3Exception()
        self.assertIsNone(requester.get_channel_by_name("Create"))
        requester.ts3conn.channelfind.side_effect = [[]]
        self.assertIsNone(requester.get_channel_by_name("Create"))

        requester.get_channel_by_name = Mock(side_effect=["1", "2"])
        requester.get_channel_group_by_name = Mock(side_effect=[4, None])
        configs = requester.parse_channel_settings(
            {
                "first.main_channel_name": "First",
                "first.channel_group_name": "Admin",
                "second.main_channel_name": "Second",
            }
        )
        self.assertEqual(configs[0]["main_channel_cid"], "1")
        requester.get_channel_by_name.side_effect = None
        requester.get_channel_by_name.return_value = "2"
        with self.assertRaises(ValueError):
            requester.parse_channel_settings(
                {
                    "second.main_channel_name": "Second",
                    "second.channel_group_name": "Missing",
                }
            )
        with self.assertRaises(ValueError):
            requester.parse_channel_settings({"malformed": "value"})

    def test_servergroup_client_lookup_and_channel_search_cover_negative_paths(self):
        requester = self._plugin()
        requester.ts3conn._parse_resp_to_list_of_dicts.return_value = [{"sgid": "7"}]
        self.assertEqual(requester.get_servergroups_by_client("8"), ["7"])
        requester.ts3conn._send.side_effect = self._query_error()
        self.assertEqual(requester.get_servergroups_by_client("8"), [])

        self.assertEqual(requester.find_channels_by_prefix(), [])
        self.assertIsNone(requester.find_channel_by_name())
        requester.ts3conn.channellist.side_effect = TS3Exception()
        with self.assertRaises(TS3Exception):
            requester.find_channels_by_prefix("Room")
        with self.assertRaises(TS3Exception):
            requester.find_channel_by_name("Room")

    def test_move_and_create_handle_missing_or_unrelated_clients(self):
        requester = self._plugin()
        requester.ts3conn.clientmove.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            requester.move_client_to_channel_id(SimpleNamespace(clid="7"), "42")
        requester.create_channel()
        requester.channel_configs = [{"main_channel_cid": "1"}]
        requester.ts3conn.clientinfo.return_value = {"client_database_id": "8"}
        requester.create_channel(SimpleNamespace(clid="7", target_channel_id="2"))
        with self.assertRaises(AttributeError):
            requester.create_channel(SimpleNamespace(target_channel_id="1"))

    def test_create_channel_respects_exclusions_and_dry_run(self):
        requester = self._plugin()
        requester.channel_configs = [
            {
                "main_channel_cid": "1",
                "main_channel_name": "Create",
                "channel_name": "Room",
            }
        ]
        requester.ts3conn.clientinfo.return_value = {
            "client_database_id": "8",
            "client_nickname": "Ada",
        }
        requester.get_channel_group_by_name = Mock(return_value=4)
        requester.find_channel_by_name = Mock(return_value=None)
        client = SimpleNamespace(clid="7", target_channel_id="1")
        with patch.object(channel_requester, "SERVERGROUPS_TO_EXCLUDE", "Support"):
            requester.servergroup_ids_to_ignore = ["9"]
            requester.get_servergroups_by_client = Mock(return_value=["9"])
            requester.create_channel(client)
        requester.ts3conn._send.assert_not_called()

        requester.get_servergroups_by_client.return_value = []
        with patch.object(channel_requester, "DRY_RUN", True):
            requester.create_channel(client)
        requester.ts3conn._send.assert_not_called()

    def test_create_channel_uses_default_group_and_renders_user_name(self):
        requester = self._plugin()
        requester.channel_configs = [
            {"main_channel_cid": "1", "main_channel_name": "Create"}
        ]
        requester.ts3conn.clientinfo.return_value = {
            "client_database_id": "8",
            "client_nickname": "Ada",
        }
        requester.get_channel_group_by_name = Mock(return_value=4)
        requester.find_channel_by_name = Mock(return_value=None)
        client = SimpleNamespace(clid="7", target_channel_id="1")
        with patch.object(channel_requester, "DRY_RUN", True):
            requester.create_channel(client)
        requester.get_channel_group_by_name.assert_called_once_with("Channel Admin")
        requester.logger.info.assert_called()

        requester.get_channel_group_by_name.return_value = None
        with patch.object(channel_requester, "DRY_RUN", True):
            requester.create_channel(client)

    def test_create_channel_propagates_create_permission_group_and_edit_errors(self):
        for failing_command in (
            "channelcreate",
            "channeladdperm",
            "setclientchannelgroup",
            "channeledit",
        ):
            requester = self._plugin()
            requester.channel_configs = [
                {
                    "main_channel_cid": "1",
                    "main_channel_name": "Create",
                    "channel_name": "Room",
                    "channel_group_id": "4",
                    "permsid": "5",
                    "channel_delete_delay": "60",
                }
            ]
            requester.ts3conn.clientinfo.return_value = {
                "client_database_id": "8",
                "client_nickname": "Ada",
            }
            requester.find_channel_by_name = Mock(return_value=None)
            requester.ts3conn._parse_resp_to_dict.return_value = {"cid": "20"}

            def fail_at_command(*args, command=failing_command):
                if args[0] == command:
                    raise self._query_error(999)

            requester.ts3conn._send.side_effect = fail_at_command
            client = SimpleNamespace(clid="7", target_channel_id="1")
            with patch.object(channel_requester, "DRY_RUN", False):
                with self.assertRaises(TS3QueryException):
                    requester.create_channel(client)

    def test_event_setup_and_version_are_exercised(self):
        requester = self._plugin()
        with patch.object(
            channel_requester.ChannelRequester, "create_channel"
        ) as create:
            with patch.object(channel_requester, "PLUGIN_INFO", requester):
                channel_requester.client_joined(SimpleNamespace(target_channel_id="1"))
        create.assert_called_once()
        bot = SimpleNamespace(ts3conn=Mock())
        with patch.object(channel_requester, "start_plugin") as start:
            channel_requester.setup(bot, auto_start=True, enable_dry_run=True)
        start.assert_called_once_with()
        with patch.object(
            channel_requester.teamspeak_bot, "send_msg_to_client"
        ) as send:
            channel_requester.BOT = bot
            channel_requester.send_version(7)
        send.assert_called_once()

    def test_parse_settings_ignores_only_missing_channel_configurations(self):
        requester = self._plugin()
        requester.ts3conn.channelfind.side_effect = [[], [{"cid": "42"}]]

        configs = requester.parse_channel_settings(
            {
                "missing.main_channel_name": "Missing",
                "available.main_channel_name": "Available",
            }
        )

        self.assertEqual(
            configs,
            [{"main_channel_name": "Available", "main_channel_cid": "42"}],
        )
        requester.logger.warning.assert_called_once()

    def test_find_channel_by_name_returns_exact_match(self):
        requester = self._plugin()
        requester.ts3conn.channellist.return_value = [
            {"channel_name": "Other", "cid": "1"},
            {"channel_name": "Requested", "cid": "2"},
        ]

        self.assertEqual(
            requester.find_channel_by_name("Requested"),
            {"channel_name": "Requested", "cid": "2"},
        )
        self.assertIsNone(requester.find_channel_by_name("Missing"))

    def test_move_client_casts_ids_before_calling_teamspeak(self):
        requester = self._plugin()

        requester.move_client_to_channel_id(SimpleNamespace(clid="7"), "42")

        requester.ts3conn.clientmove.assert_called_once_with(42, 7)

    def test_channel_group_lookup_skips_templates_and_query_groups(self):
        requester = self._plugin()
        requester.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"type": "0", "name": "Channel Admin", "cgid": "1"},
            {"type": "2", "name": "Channel Admin", "cgid": "2"},
            {"type": "1", "name": "Channel Admin", "cgid": "3"},
        ]

        self.assertEqual(requester.get_channel_group_by_name("Channel Admin"), 3)

    def test_servergroup_exclusions_skip_templates(self):
        requester = self._plugin()
        requester.ts3conn.servergrouplist.return_value = [
            {"type": "0", "sgid": "1", "name": "Support"},
            {"type": "1", "sgid": "2", "name": "Support"},
            {"type": "1", "sgid": "3", "name": "Other"},
        ]

        with patch.object(
            channel_requester,
            "SERVERGROUPS_TO_EXCLUDE",
            "Support",
        ):
            self.assertEqual(requester.update_servergroup_ids_list(), ["2"])

    def test_create_channel_moves_client_to_existing_private_channel(self):
        requester = self._plugin()
        requester.channel_configs = [
            {
                "main_channel_cid": "1",
                "main_channel_name": "Create",
                "channel_group_id": "4",
                "channel_name": "Room",
            }
        ]
        requester.ts3conn.clientinfo.return_value = {
            "client_database_id": "8",
            "client_nickname": "Ada",
        }
        requester.find_channel_by_name = Mock(return_value={"cid": "9"})
        client = SimpleNamespace(clid="7", target_channel_id="1")

        requester.create_channel(client)

        requester.ts3conn.clientmove.assert_called_once_with(9, 7)

    def test_create_channel_creates_private_channel_and_permissions(self):
        requester = self._plugin()
        requester.channel_configs = [
            {
                "main_channel_cid": "1",
                "main_channel_name": "Create",
                "channel_name": "Room %i",
                "channel_group_id": "4",
                "channel_delete_delay": "60",
                "channel_description": "A room",
                "permsid": "5",
            }
        ]
        requester.ts3conn.clientinfo.return_value = {
            "client_database_id": "8",
            "client_nickname": "Ada",
        }
        requester.find_channel_by_name = Mock(return_value=None)
        requester.find_channels_by_prefix = Mock(
            return_value=[{"channel_name": "Room 1", "cid": "10"}]
        )
        requester.ts3conn._parse_resp_to_dict.return_value = {"cid": "20"}
        requester.move_client_to_channel_id = Mock()
        client = SimpleNamespace(clid="7", target_channel_id="1")

        with patch.object(channel_requester, "DRY_RUN", False):
            requester.create_channel(client)

        requester.ts3conn._send.assert_any_call(
            "channelcreate",
            [
                "channel_flag_semi_permanent=1",
                "cpid=1",
                "channel_name=Room 2",
                "channel_description=A room",
            ],
        )
        requester.ts3conn._send.assert_any_call(
            "channeladdperm", ["cid=20", "permsid=permsid", "permvalue=5"]
        )
        requester.move_client_to_channel_id.assert_called_once_with(client, "20")
        requester.ts3conn._send.assert_any_call(
            "setclientchannelgroup", ["cgid=4", "cid=20", "cldbid=8"]
        )
        requester.ts3conn._send.assert_any_call(
            "channeledit",
            ["cid=20", "channel_flag_semi_permanent=0", "channel_delete_delay=60"],
        )


if __name__ == "__main__":
    unittest.main()
