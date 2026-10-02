"""Unit tests for the poke-on-channel-join plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from tests.support import (
    TS3Exception,
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.poke_client_on_channel_join import main as poke_on_join


class PokeClientOnChannelJoinTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = poke_on_join.PokeClientOnChannelJoin.__new__(
            poke_on_join.PokeClientOnChannelJoin
        )
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        return plugin

    @staticmethod
    def _query_error():
        return TS3QueryException()

    def test_channel_lookup_handles_sdk_errors_and_missing_channels(self):
        plugin = self._plugin()
        plugin.ts3conn.channelfind.side_effect = TS3Exception()
        self.assertIsNone(plugin.get_channel_by_name("Support"))

        plugin.ts3conn.channelfind.side_effect = [[]]
        self.assertIsNone(plugin.get_channel_by_name("Support"))

    def test_parse_settings_rejects_invalid_keys_and_filters_missing_channels(self):
        plugin = self._plugin()
        plugin.get_channel_by_name = Mock(return_value=None)
        self.assertEqual(
            plugin.parse_channel_settings({"support.channel_name": "Support"}), []
        )

        with self.assertRaises(ValueError):
            plugin.parse_channel_settings({"invalid": "value"})

    def test_servergroup_lookup_returns_members_and_handles_missing_or_errors(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.side_effect = [
            [{"name": "Supporters", "sgid": "7"}],
            [{"cldbid": "42"}, {"cldbid": "43"}],
        ]
        self.assertEqual(
            plugin.get_servergroup_client_database_ids("Supporters"), ["42", "43"]
        )
        self.assertEqual(
            plugin.ts3conn._send.call_args_list,
            [
                call("servergrouplist"),
                call("servergroupclientlist", ["sgid=7"]),
            ],
        )

        plugin.ts3conn._parse_resp_to_list_of_dicts.side_effect = [
            [{"name": "Other", "sgid": "1"}]
        ]
        self.assertEqual(plugin.get_servergroup_client_database_ids("Supporters"), [])
        plugin.ts3conn._send.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.get_servergroup_client_database_ids("Supporters")

    def test_poke_client_supports_dry_run_and_propagates_query_errors(self):
        plugin = self._plugin()
        info = {"client_database_id": "7", "client_nickname": "Ada"}
        with patch.object(poke_on_join, "DRY_RUN", True):
            plugin.poke_client(42, info, "Hello")
        plugin.ts3conn.clientpoke.assert_not_called()

        plugin.ts3conn.clientpoke.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.poke_client(42, info, "Hello")

    def test_team_pokes_render_newbie_and_recipient_placeholders(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "1", "client_database_id": "1", "clid": "10"},
            {"client_type": "0", "client_database_id": "7", "clid": "20"},
            {"client_type": "0", "client_database_id": "8", "clid": "30"},
        ]
        plugin.ts3conn.clientinfo.return_value = {
            "client_nickname": "Supporter",
            "client_database_id": "99",
        }
        plugin.poke_servergroup_clients(
            {
                "team_poke_message": "%c joined; hello %u",
                "team_client_database_ids": ["7"],
            },
            {"client_nickname": "Newbie"},
        )
        plugin.ts3conn.clientinfo.assert_called_once_with(20)
        plugin.ts3conn.clientpoke.assert_called_once_with(
            20, "Newbie joined; hello Supporter"
        )

    def test_team_poke_without_user_placeholder_and_missing_config(self):
        plugin = self._plugin()
        plugin.poke_client = Mock()
        plugin.poke_servergroup_clients(
            {"team_client_database_ids": []}, {"client_nickname": "Newbie"}
        )
        plugin.ts3conn.clientlist.assert_not_called()

        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "client_database_id": "7", "clid": "20"}
        ]
        plugin.poke_servergroup_clients(
            {"team_poke_message": "Newbie joined", "team_client_database_ids": ["7"]},
            {"client_nickname": "Newbie"},
        )
        plugin.poke_client.assert_called_once_with(
            client_id=20,
            client_info={"client_nickname": "Newbie"},
            poke_message="Newbie joined",
        )

        plugin.ts3conn.clientlist.side_effect = TS3Exception()
        with self.assertRaises(TS3Exception):
            plugin.poke_servergroup_clients(
                {"team_poke_message": "Newbie", "team_client_database_ids": []},
                {"client_nickname": "Newbie"},
            )

    def test_poke_user_without_message_is_ignored(self):
        plugin = self._plugin()
        plugin.poke_client = Mock()
        plugin.poke_user({}, 42, {"client_nickname": "Ada"})
        plugin.poke_client.assert_not_called()

        plugin.poke_user(
            {"user_poke_message": "Welcome"}, 42, {"client_nickname": "Ada"}
        )
        plugin.poke_client.assert_called_once_with(
            client_id=42,
            client_info={"client_nickname": "Ada"},
            poke_message="Welcome",
        )

    def test_run_handles_unrelated_and_invalid_events(self):
        plugin = self._plugin()
        plugin.channel_configs = [
            {
                "channel_id": "42",
                "channel_name": "Support",
            }
        ]
        plugin.run(None)
        plugin.run(SimpleNamespace(target_channel_id="99", clid=7))
        with self.assertRaises(ValueError):
            plugin.run(SimpleNamespace(target_channel_id="42", clid=7))

        plugin.channel_configs[0]["user_poke_message"] = "Hi"
        plugin.ts3conn.clientinfo.side_effect = TS3Exception()
        with self.assertRaises(TS3Exception):
            plugin.run(SimpleNamespace(target_channel_id="42", clid=7))

        with self.assertRaises(AttributeError):
            plugin.run(SimpleNamespace(target_channel_id="42"))

    def test_run_executes_both_poke_paths(self):
        plugin = self._plugin()
        plugin.channel_configs = [
            {
                "channel_id": "42",
                "channel_name": "Support",
                "team_poke_message": "Team",
                "user_poke_message": "User %u",
            }
        ]
        plugin.ts3conn.clientinfo.return_value = {
            "client_nickname": "Ada",
            "client_database_id": "7",
        }
        plugin.poke_servergroup_clients = Mock()
        plugin.poke_user = Mock()
        client = SimpleNamespace(target_channel_id="42", clid=7)
        plugin.run(client)
        plugin.poke_servergroup_clients.assert_called_once()
        plugin.poke_user.assert_called_once()

    def test_event_and_setup_delegate_to_plugin(self):
        plugin = self._plugin()
        plugin.channel_configs = []
        with patch.object(poke_on_join.PokeClientOnChannelJoin, "run") as run:
            with patch.object(poke_on_join, "PLUGIN_INFO", plugin):
                poke_on_join.client_joined(SimpleNamespace(target_channel_id="42"))
        run.assert_called_once()

        with patch.object(poke_on_join, "PLUGIN_INFO", None):
            poke_on_join.client_joined(SimpleNamespace(target_channel_id="42"))

        bot = SimpleNamespace(ts3conn=Mock())
        poke_on_join.setup(bot, auto_start=False, support_channel_name="Support")
        self.assertIs(poke_on_join.BOT, bot)
        self.assertEqual(
            poke_on_join.CHANNEL_SETTINGS, {"support_channel_name": "Support"}
        )

    def test_parse_settings_resolves_channel_and_team_members(self):
        plugin = self._plugin()
        plugin.get_channel_by_name = Mock(return_value="42")
        plugin.get_servergroup_client_database_ids = Mock(return_value=["7", "8"])

        configs = plugin.parse_channel_settings(
            {
                "support.channel_name": "Support Lobby",
                "support.team_servergroups": "Supporters",
                "support.user_poke_message": "Welcome %u",
            }
        )

        self.assertEqual(
            configs,
            [
                {
                    "team_client_database_ids": ["7", "8"],
                    "channel_name": "Support Lobby",
                    "channel_id": "42",
                    "team_servergroups": "Supporters",
                    "user_poke_message": "Welcome %u",
                }
            ],
        )

    def test_parse_settings_initializes_team_members_for_each_alias(self):
        plugin = self._plugin()
        plugin.get_channel_by_name = Mock(side_effect=["42", "43"])
        plugin.get_servergroup_client_database_ids = Mock(side_effect=[["7"], ["8"]])

        configs = plugin.parse_channel_settings(
            {
                "first.channel_name": "First",
                "first.team_servergroups": "Supporters",
                "second.channel_name": "Second",
                "second.team_servergroups": "Moderators",
            }
        )

        self.assertEqual(
            [config["team_client_database_ids"] for config in configs],
            [["7"], ["8"]],
        )

    def test_poke_user_replaces_user_placeholder(self):
        plugin = self._plugin()
        client_info = {"client_nickname": "Ada", "client_database_id": "7"}

        plugin.poke_user({"user_poke_message": "Welcome %u"}, 42, client_info)

        plugin.ts3conn.clientpoke.assert_called_once_with(42, "Welcome Ada")

    def test_poke_servergroup_clients_excludes_serverquery_clients(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "client_database_id": "7", "clid": "10"},
            {"client_type": "1", "client_database_id": "8", "clid": "20"},
        ]
        plugin.poke_client = Mock()

        plugin.poke_servergroup_clients(
            {
                "team_poke_message": "Newbie joined",
                "team_client_database_ids": ["7"],
            },
            {"client_nickname": "Newbie"},
        )

        plugin.poke_client.assert_called_once_with(
            client_id=10,
            client_info={"client_nickname": "Newbie"},
            poke_message="Newbie joined",
        )


if __name__ == "__main__":
    unittest.main()
