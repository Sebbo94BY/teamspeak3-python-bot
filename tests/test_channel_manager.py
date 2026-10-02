"""Unit tests for the channel manager plugin."""

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

from modules.channel_manager import main as channel_manager


class ChannelManagerTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = channel_manager.ChannelManager.__new__(channel_manager.ChannelManager)
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        return plugin

    @staticmethod
    def _query_error(error_id=None):
        error = TS3QueryException()
        if error_id is not None:
            error.id = error_id
        return error

    def test_parser_and_channel_lookup_cover_invalid_and_missing_values(self):
        manager = self._plugin()
        with self.assertRaises(ValueError):
            manager.parse_channel_settings({"malformed": "value"})
        manager.ts3conn.channelfind.side_effect = TS3QueryException()
        self.assertIsNone(manager.get_channel_id_by_name_pattern("Support"))
        manager.ts3conn.channelfind.side_effect = [[]]
        self.assertIsNone(manager.get_channel_id_by_name_pattern("Support"))

    def test_channel_listing_covers_none_and_query_error_paths(self):
        manager = self._plugin()
        manager.channel_configs = []
        self.assertEqual(manager.find_channels_by_prefix(), [])
        manager.ts3conn.channellist.side_effect = TS3QueryException()
        with self.assertRaises(TS3QueryException):
            manager.find_channels_by_prefix("Room")

    def test_minimum_channel_creation_skips_complete_configuration(self):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "1",
            }
        ]
        manager.channel_minimums = {"Room": 1}
        manager.find_channels_by_prefix = Mock(
            return_value=[{"channel_name": "Room 1", "total_clients": "0"}]
        )
        manager.create_minimum_amount_of_channels()
        manager.ts3conn._send.assert_not_called()

    def test_minimum_channel_creation_handles_race_and_permission_errors(self):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "1",
                "permsid": "5",
            }
        ]
        manager.channel_minimums = {"Room": 1}
        manager.find_channels_by_prefix = Mock(return_value=[])
        manager.ts3conn._send.side_effect = self._query_error(771)
        manager.create_minimum_amount_of_channels()
        manager.ts3conn._send.side_effect = self._query_error(999)
        with self.assertRaises(TS3QueryException):
            manager.create_minimum_amount_of_channels()

    def test_create_channel_when_necessary_handles_unmanaged_empty_and_invalid_events(
        self,
    ):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "1",
            }
        ]
        manager.find_channels_by_prefix = Mock(return_value=[])
        manager.ts3conn._parse_resp_to_dict.return_value = {"channel_name": "Other"}
        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id="1"))
        manager.ts3conn._send.side_effect = self._query_error(768)
        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id="1"))
        manager.ts3conn._send.side_effect = self._query_error(999)
        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id="1"))

        manager.ts3conn._send.side_effect = None
        manager.ts3conn._parse_resp_to_dict.return_value = {"channel_name": "Room 1"}
        manager.find_channels_by_prefix.return_value = [
            {"channel_name": "Room 1", "total_clients": "0", "cid": "3"}
        ]
        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id="1"))
        manager.ts3conn._send.assert_called()

    def test_create_channel_when_necessary_handles_create_race_and_dry_run(self):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "1",
                "permsid": "5",
            }
        ]
        manager.find_channels_by_prefix = Mock(
            return_value=[{"channel_name": "Room 1", "total_clients": "1", "cid": "3"}]
        )
        manager.ts3conn._parse_resp_to_dict.return_value = {"channel_name": "Room 1"}

        def fail_channel_create(*args):
            if args[0] == "channelcreate":
                raise self._query_error(771)

        manager.ts3conn._send.side_effect = fail_channel_create
        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id="3"))

        manager.ts3conn._send.side_effect = None
        with patch.object(channel_manager, "DRY_RUN", True):
            manager.create_channel_when_necessary(
                SimpleNamespace(target_channel_id="3")
            )

    def test_channel_stats_and_delete_cover_dry_run_and_delete_race(self):
        manager = self._plugin()
        manager.channel_configs = [{"name_prefix": "Room", "minimum": "1"}]
        manager.find_channels_by_prefix = Mock(
            return_value=[
                {"channel_name": "Room 1", "cid": "1", "total_clients": "0"},
                {"channel_name": "Room 2", "cid": "2", "total_clients": "0"},
            ]
        )
        self.assertEqual(len(manager.get_channel_stats()["Room"]), 2)
        manager.channel_minimums = {"Room": 1}
        with patch.object(channel_manager, "DRY_RUN", True):
            manager.delete_channel_when_necessary()
        manager.ts3conn._send.reset_mock()
        manager.ts3conn._send.side_effect = self._query_error(768)
        manager.delete_channel_when_necessary()
        manager.ts3conn._send.side_effect = self._query_error(999)
        with self.assertRaises(TS3QueryException):
            manager.delete_channel_when_necessary()

    def test_events_setup_and_version_are_exercised(self):
        manager = self._plugin()
        with (
            patch.object(channel_manager, "PLUGIN_INFO", manager),
            patch.object(
                channel_manager.ChannelManager, "create_channel_when_necessary"
            ) as create,
            patch.object(
                channel_manager.ChannelManager, "delete_channel_when_necessary"
            ) as delete,
        ):
            channel_manager.client_entered_left_moved_event(
                SimpleNamespace(target_channel_id="1")
            )
            channel_manager.client_kicked_banned_event(SimpleNamespace())
        create.assert_called_once()
        self.assertEqual(delete.call_count, 2)

        bot = SimpleNamespace(ts3conn=Mock())
        with patch.object(channel_manager, "start_plugin") as start:
            channel_manager.setup(bot, auto_start=True, room_parent_channel="Support")
        start.assert_called_once_with()
        with patch.object(channel_manager.teamspeak_bot, "send_msg_to_client") as send:
            channel_manager.BOT = bot
            channel_manager.send_version(7)
        send.assert_called_once()

    def test_parse_settings_groups_by_alias_and_resolves_parent(self):
        manager = self._plugin()
        manager.get_channel_id_by_name_pattern = Mock(return_value=10)

        configs = manager.parse_channel_settings(
            {
                "support.parent_channel_name": "Support",
                "support.name_prefix": "Room ",
                "support.minimum": "2",
            }
        )

        self.assertEqual(
            configs,
            [
                {
                    "parent_channel_name": "Support",
                    "parent_channel_id": 10,
                    "name_prefix": "Room ",
                    "minimum": "2",
                }
            ],
        )

    def test_channel_minimums_are_ints_and_do_not_mutate_configs(self):
        manager = self._plugin()
        manager.channel_configs = [
            {"name_prefix": "Room ", "minimum": "3", "other": "kept"}
        ]

        self.assertEqual(manager.get_channel_minimums(), {"Room ": 3})
        self.assertEqual(manager.channel_configs[0]["other"], "kept")

    def test_find_channels_by_prefix_filters_names(self):
        manager = self._plugin()
        manager.ts3conn.channellist.return_value = [
            {"channel_name": "Room 1"},
            {"channel_name": "Other"},
        ]

        self.assertEqual(
            manager.find_channels_by_prefix("Room"), [{"channel_name": "Room 1"}]
        )

    def test_unexpected_channel_info_error_uses_fallback_path(self):
        manager = self._plugin()
        manager.channel_configs = []
        manager.managed_channels = []
        manager.find_channels_by_prefix = Mock(return_value=[])
        error = TS3QueryException()
        error.id = 999
        manager.ts3conn._send.side_effect = error

        manager.create_channel_when_necessary(SimpleNamespace(target_channel_id=1))

        manager.find_channels_by_prefix.assert_called_once_with()

    def test_create_minimum_channels_in_dry_run_with_permissions(self):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "2",
                "channel_description": "line\\nnext",
                "permsid": "5",
            }
        ]
        manager.channel_minimums = {"Room": 2}
        manager.find_channels_by_prefix = Mock(
            return_value=[{"channel_name": "Room 1", "total_clients": "1"}]
        )

        with patch.object(channel_manager, "DRY_RUN", True):
            manager.create_minimum_amount_of_channels()

        self.assertEqual(manager.find_channels_by_prefix.call_count, 1)
        self.assertEqual(manager.ts3conn._send.call_count, 0)
        self.assertTrue(manager.logger.info.called)

    def test_create_minimum_channels_sends_permissions(self):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "1",
                "permsid": "5",
            }
        ]
        manager.channel_minimums = {"Room": 1}
        manager.find_channels_by_prefix = Mock(return_value=[])
        manager.ts3conn._parse_resp_to_dict.return_value = {"cid": "9"}

        with patch.object(channel_manager, "DRY_RUN", False):
            manager.create_minimum_amount_of_channels()

        manager.ts3conn._send.assert_any_call(
            "channelcreate",
            [
                "cpid=1",
                "channel_flag_semi_permanent=1",
                "channel_name=Room 1",
            ],
        )
        manager.ts3conn._send.assert_any_call(
            "channeladdperm", ["cid=9", "permsid=permsid", "permvalue=5"]
        )

    def test_create_channel_when_all_managed_channels_are_occupied(self):
        manager = self._plugin()
        manager.channel_configs = [
            {
                "parent_channel_name": "Support",
                "parent_channel_id": "1",
                "name_prefix": "Room",
                "minimum": "1",
                "channel_description": "description",
                "permsid": "5",
            }
        ]
        manager.find_channels_by_prefix = Mock(
            return_value=[{"channel_name": "Room 1", "total_clients": "1", "cid": "3"}]
        )
        manager.ts3conn._parse_resp_to_dict.side_effect = [
            {"channel_name": "Room 1"},
            {"cid": "9"},
        ]

        with patch.object(channel_manager, "DRY_RUN", False):
            manager.create_channel_when_necessary(SimpleNamespace(target_channel_id=3))

        manager.ts3conn._send.assert_any_call(
            "channelcreate",
            [
                "cpid=1",
                "channel_flag_semi_permanent=1",
                "channel_name=Room 2",
                "channel_order=3",
                "channel_description=description",
            ],
        )
        manager.ts3conn._send.assert_any_call(
            "channeladdperm", ["cid=9", "permsid=permsid", "permvalue=5"]
        )

    def test_delete_channel_keeps_one_empty_channel(self):
        manager = self._plugin()
        manager.channel_minimums = {"Room": 1}
        channels = [
            {"channel_name": "Room 1", "cid": "1", "total_clients": "0"},
            {"channel_name": "Room 2", "cid": "2", "total_clients": "0"},
        ]
        manager.get_channel_stats = Mock(return_value={"Room": channels})

        with patch.object(channel_manager, "DRY_RUN", False):
            manager.delete_channel_when_necessary()

        manager.ts3conn._send.assert_called_once_with("channeldelete", ["cid=2"])


if __name__ == "__main__":
    unittest.main()
