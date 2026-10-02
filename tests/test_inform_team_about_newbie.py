"""Unit tests for the newbie notification plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.support import (
    TS3Exception,
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.inform_team_about_newbie import main as newbie_notifier


class InformTeamAboutNewbieTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = newbie_notifier.InformTeamAboutNewbie.__new__(
            newbie_notifier.InformTeamAboutNewbie
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

    def test_constructor_resolves_all_configured_dependencies(self):
        connection = Mock()
        with (
            patch.object(newbie_notifier, "NEWBIE_SERVERGROUP_NAME", "Guest"),
            patch.object(newbie_notifier, "SUPPORT_CHANNEL_NAME", "Support"),
            patch.object(newbie_notifier, "MOVE_DELAY_SECONDS", "3"),
            patch.object(newbie_notifier, "TEAM_SERVERGROUP_NAMES", "Moderator,Helper"),
            patch.object(
                newbie_notifier.InformTeamAboutNewbie,
                "get_servergroup_by_name",
                side_effect=[{"sgid": "1"}, {"sgid": "2"}, {"sgid": "3"}],
            ),
            patch.object(
                newbie_notifier.InformTeamAboutNewbie,
                "get_channel_by_name",
                return_value={"cid": "9"},
            ),
        ):
            plugin = newbie_notifier.InformTeamAboutNewbie(Mock(), connection)

        self.assertEqual(plugin.newbie_servergroup, {"sgid": "1"})
        self.assertEqual(plugin.support_channel, {"cid": "9"})
        self.assertEqual(plugin.move_delay_seconds, 3)
        self.assertEqual(plugin.team_servergroups, [{"sgid": "2"}, {"sgid": "3"}])

    def test_constructor_rejects_invalid_move_delay(self):
        with (
            patch.object(newbie_notifier, "MOVE_DELAY_SECONDS", "not-a-number"),
            patch.object(
                newbie_notifier.InformTeamAboutNewbie,
                "get_servergroup_by_name",
                return_value={"sgid": "1"},
            ),
        ):
            with self.assertRaises(ValueError):
                newbie_notifier.InformTeamAboutNewbie(Mock(), Mock())

    def test_servergroup_lookup_skips_templates_and_propagates_sdk_errors(self):
        plugin = self._plugin()
        with patch.object(
            newbie_notifier,
            "get_servergroups",
            return_value=[
                {"type": "0", "name": "Guest"},
                {"type": "1", "name": "Guest", "sgid": "7"},
            ],
        ):
            self.assertEqual(plugin.get_servergroup_by_name("Guest")["sgid"], "7")

        with patch.object(
            newbie_notifier, "get_servergroups", side_effect=TS3Exception()
        ):
            with self.assertRaises(TS3Exception):
                plugin.get_servergroup_by_name("Guest")

    def test_channel_lookup_handles_sdk_errors(self):
        plugin = self._plugin()
        plugin.ts3conn.channelfind.side_effect = TS3Exception()
        self.assertIsNone(plugin.get_channel_by_name("Support"))
        plugin.logger.exception.assert_called_once()

    def test_servergroup_membership_lookup_handles_success_and_query_error(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [
            {"sgid": "1"},
            {"sgid": "2"},
        ]
        self.assertEqual(plugin.get_servergroups_by_client("7"), ["1", "2"])
        plugin.ts3conn._send.assert_called_with("servergroupsbyclientid", ["cldbid=7"])

        plugin.ts3conn._send.side_effect = self._query_error()
        self.assertEqual(plugin.get_servergroups_by_client("7"), [])

    def test_move_newbie_supports_dry_run_and_delayed_move(self):
        plugin = self._plugin()
        plugin.support_channel = {"cid": "9"}
        plugin.move_delay_seconds = 2
        newbie = SimpleNamespace(client_dbid="7", client_id="42", client_name="Ada")
        sleep = AsyncMock()

        with patch.object(newbie_notifier, "asyncio") as async_module:
            async_module.sleep = sleep
            asyncio.run(plugin.move_newbie_to_support(newbie))
        sleep.assert_awaited_once_with(2)
        plugin.ts3conn.clientmove.assert_called_once_with(9, 42)

        plugin.ts3conn.reset_mock()
        with patch.object(newbie_notifier, "DRY_RUN", True):
            asyncio.run(plugin.move_newbie_to_support(newbie))
        plugin.ts3conn.clientmove.assert_not_called()

    def test_move_newbie_ignores_departed_client_but_raises_other_errors(self):
        plugin = self._plugin()
        plugin.support_channel = {"cid": "9"}
        plugin.move_delay_seconds = None
        newbie = SimpleNamespace(client_dbid="7", client_id="42", client_name="Ada")

        plugin.ts3conn.clientmove.side_effect = self._query_error(512)
        asyncio.run(plugin.move_newbie_to_support(newbie))
        plugin.ts3conn.clientmove.side_effect = self._query_error(999)
        with self.assertRaises(TS3QueryException):
            asyncio.run(plugin.move_newbie_to_support(newbie))

    def test_team_member_list_combines_multiple_groups(self):
        plugin = self._plugin()
        plugin.team_servergroups = [{"sgid": "1"}, {"sgid": "2"}]
        plugin.ts3conn._parse_resp_to_list_of_dicts.side_effect = [
            [{"cldbid": "7"}],
            [{"cldbid": "8"}],
        ]
        self.assertEqual(plugin.get_team_member_list(), ["7", "8"])

        plugin.ts3conn._send.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.get_team_member_list()

    def test_poke_team_dry_run_and_query_failure(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "1", "client_database_id": "1"},
            {"client_type": "0", "client_database_id": "2", "clid": "20"},
            {"client_type": "0", "client_database_id": "3", "clid": "30"},
        ]
        plugin.get_team_member_list = Mock(return_value=["2"])
        plugin.team_poke_message = "Newbie joined"
        with patch.object(newbie_notifier, "DRY_RUN", True):
            plugin.poke_team(SimpleNamespace(client_name="Ada"))
        plugin.ts3conn.clientpoke.assert_not_called()

        plugin.ts3conn.clientlist.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.poke_team(SimpleNamespace(client_name="Ada"))

    def test_poke_team_propagates_poke_errors(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "client_database_id": "2", "clid": "20"}
        ]
        plugin.get_team_member_list = Mock(return_value=["2"])
        plugin.team_poke_message = "Newbie joined"
        plugin.ts3conn.clientpoke.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.poke_team(SimpleNamespace(client_name="Ada"))

    def test_poke_newbie_renders_name_and_supports_dry_run(self):
        plugin = self._plugin()
        newbie = SimpleNamespace(client_dbid="7", client_id="42", client_name="Ada")
        plugin.newbie_poke_message = "Hello %u"
        plugin.poke_newbie(newbie)
        plugin.ts3conn.clientpoke.assert_called_once_with("42", "Hello Ada")

        plugin.ts3conn.reset_mock()
        with patch.object(newbie_notifier, "DRY_RUN", True):
            plugin.poke_newbie(newbie)
        plugin.ts3conn.clientpoke.assert_not_called()

        plugin.ts3conn.clientpoke.side_effect = self._query_error()
        with self.assertRaises(TS3QueryException):
            plugin.poke_newbie(newbie)

    def test_check_joined_client_runs_complete_newbie_flow(self):
        plugin = self._plugin()
        plugin.newbie_servergroup = {"sgid": "5", "name": "Guest"}
        plugin.support_channel = {"cid": "9"}
        client = SimpleNamespace(
            client_uid="uid", client_dbid="7", client_id="42", client_name="Ada"
        )
        plugin.get_servergroups_by_client = Mock(return_value=["5"])
        plugin.poke_team = Mock()
        plugin.poke_newbie = Mock()
        plugin.move_newbie_to_support = Mock()
        with patch.object(newbie_notifier.asyncio, "run") as run:
            plugin.check_joined_client(client)
        plugin.poke_team.assert_called_once_with(newbie_client=client)
        plugin.poke_newbie.assert_called_once_with(newbie_client=client)
        run.assert_called_once()

        plugin.get_servergroups_by_client.return_value = []
        plugin.poke_team.reset_mock()
        plugin.check_joined_client(client)
        plugin.poke_team.assert_not_called()

    def test_setup_validates_message_lengths(self):
        bot = SimpleNamespace()
        with self.assertRaises(ValueError):
            newbie_notifier.setup(bot, auto_start=False, newbie_poke_message="x" * 101)
        with self.assertRaises(ValueError):
            newbie_notifier.setup(bot, auto_start=False, team_poke_message="x" * 101)

    def test_event_and_lifecycle_functions_delegate(self):
        plugin = self._plugin()
        with patch.object(newbie_notifier, "PLUGIN_INFO", plugin):
            with patch.object(
                newbie_notifier.InformTeamAboutNewbie, "check_joined_client"
            ) as check:
                newbie_notifier.client_joined(SimpleNamespace(client_name="Ada"))
        check.assert_called_once()

        with patch.object(newbie_notifier.teamspeak_bot, "send_msg_to_client") as send:
            newbie_notifier.BOT = SimpleNamespace(ts3conn=Mock())
            newbie_notifier.send_version(7)
        send.assert_called_once()

        plugin.join = Mock()
        with patch.object(newbie_notifier, "PLUGIN_INFO", plugin):
            newbie_notifier.stop_plugin()
        plugin.join.assert_called_once()

    def test_team_poke_renders_each_recipient_name_and_count(self):
        notifier = self._plugin()
        notifier.ts3conn.clientlist.return_value = [
            {
                "client_type": "0",
                "client_database_id": "1",
                "clid": "10",
                "client_nickname": "Ada",
            },
            {
                "client_type": "0",
                "client_database_id": "2",
                "clid": "20",
                "client_nickname": "Lin",
            },
        ]
        notifier.get_team_member_list = Mock(return_value=["1", "2"])
        notifier.team_poke_message = "%u: %n (%c online)"

        original_dry_run = newbie_notifier.DRY_RUN
        newbie_notifier.DRY_RUN = False
        try:
            notifier.poke_team(SimpleNamespace(client_name="Newbie"))
        finally:
            newbie_notifier.DRY_RUN = original_dry_run

        self.assertEqual(
            notifier.ts3conn.clientpoke.call_args_list,
            [
                (("10", "Ada: Newbie (2 online)"),),
                (("20", "Lin: Newbie (2 online)"),),
            ],
        )

    def test_team_poke_skips_when_no_team_member_is_online(self):
        notifier = self._plugin()
        notifier.ts3conn.clientlist.return_value = []
        notifier.get_team_member_list = Mock(return_value=[])
        notifier.team_poke_message = "%u: %n"

        notifier.poke_team(SimpleNamespace(client_name="Newbie"))

        notifier.ts3conn.clientpoke.assert_not_called()

    def test_serverquery_clients_are_ignored(self):
        notifier = self._plugin()
        notifier.get_servergroups_by_client = Mock()
        notifier.check_joined_client(
            SimpleNamespace(
                client_uid="ServerQuery", client_name="query_1", client_dbid="1"
            )
        )

        notifier.get_servergroups_by_client.assert_not_called()

    def test_missing_channel_is_returned_as_none(self):
        notifier = self._plugin()
        notifier.ts3conn.channelfind.return_value = []

        self.assertIsNone(notifier.get_channel_by_name("Support"))
        notifier.logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
