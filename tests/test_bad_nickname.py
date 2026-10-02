"""Unit tests for the bad-nickname plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import re
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

from modules.bad_nickname import main as bad_nickname


class BadNicknameTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = bad_nickname.BadNickname.__new__(bad_nickname.BadNickname)
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        plugin.servergroup_ids_to_ignore = []
        return plugin

    @staticmethod
    def _query_error():
        return TS3QueryException()

    def test_pattern_parser_rejects_unknown_options_and_malformed_keys(self):
        plugin = self._plugin()
        with self.assertRaises(ValueError):
            plugin.parse_bad_name_patterns({"spam.unknown": "spam"})
        with self.assertRaises(ValueError):
            plugin.parse_bad_name_patterns({"malformed": "spam"})

    def test_pattern_parser_keeps_valid_patterns_after_invalid_regex(self):
        plugin = self._plugin()
        patterns = plugin.parse_bad_name_patterns(
            {"broken.name_pattern": "[", "valid.name_pattern": "ok"}
        )
        self.assertEqual([pattern["regex_alias"] for pattern in patterns], ["valid"])

    def test_update_client_list_handles_sdk_errors(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.side_effect = TS3Exception()
        plugin.update_client_list()
        self.assertEqual(plugin.client_list, [])

    def test_servergroup_exclusion_lookup_handles_templates_and_query_errors(self):
        plugin = self._plugin()
        with (
            patch.object(bad_nickname, "SERVERGROUPS_TO_EXCLUDE", "VIP"),
            patch.object(
                bad_nickname,
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
            patch.object(bad_nickname, "SERVERGROUPS_TO_EXCLUDE", "VIP"),
            patch.object(
                bad_nickname, "get_servergroups", side_effect=self._query_error()
            ),
        ):
            plugin.update_servergroup_ids_list()

    def test_servergroup_membership_lookup_handles_success_and_errors(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [{"sgid": "2"}]
        self.assertEqual(plugin.get_servergroups_by_client("7"), ["2"])
        plugin.ts3conn._send.side_effect = self._query_error()
        self.assertEqual(plugin.get_servergroups_by_client("7"), [])

    def test_bad_nickname_filter_skips_malformed_clients_and_exclusions(self):
        plugin = self._plugin()
        plugin.client_list = [
            {"client_type": "0", "client_nickname": "spam"},
            {"cid": "1", "client_type": "0"},
            {"cid": "1", "client_type": "1", "client_nickname": "spam"},
            {
                "cid": "1",
                "client_type": "0",
                "client_nickname": "spam",
                "client_database_id": "7",
            },
        ]
        plugin.bad_name_patterns = [
            {"regex_alias": "spam", "regex_object": re.compile("spam")}
        ]
        with patch.object(bad_nickname, "SERVERGROUPS_TO_EXCLUDE", "VIP"):
            plugin.servergroup_ids_to_ignore = ["2"]
            plugin.get_servergroups_by_client = Mock(return_value=["2"])
            self.assertEqual(plugin.get_client_list_with_bad_nickname(), [])

    def test_bad_nickname_kick_supports_dry_run_and_errors(self):
        plugin = self._plugin()
        bad_client = {"clid": "42", "cid": "1", "client_nickname": "spam"}
        plugin.get_client_list_with_bad_nickname = Mock(return_value=[bad_client])
        with patch.object(bad_nickname, "DRY_RUN", True):
            plugin.kick_all_clients_with_bad_nickname()
        plugin.ts3conn.clientkick.assert_not_called()

        plugin.ts3conn.clientkick.side_effect = TS3Exception()
        plugin.kick_all_clients_with_bad_nickname()

        plugin.get_client_list_with_bad_nickname.return_value = None
        plugin.kick_all_clients_with_bad_nickname()

    def test_loop_runs_and_catches_errors(self):
        plugin = self._plugin()
        plugin.stopped = Mock()
        plugin.stopped.wait.side_effect = [False, True]
        plugin.update_client_list = Mock()
        plugin.kick_all_clients_with_bad_nickname = Mock()
        plugin.loop_until_stopped()
        plugin.update_client_list.assert_called_once()
        plugin.kick_all_clients_with_bad_nickname.assert_called_once()

        plugin.stopped.wait.side_effect = [False, True]
        plugin.update_client_list.side_effect = RuntimeError("broken")
        plugin.loop_until_stopped()

    def test_setup_validates_kick_reason_before_starting(self):
        bot = type("Bot", (), {"ts3conn": Mock()})()
        bad_nickname.setup(bot, auto_start=False, kick_reason_message="x" * 41)
        with patch.object(bad_nickname, "BadNickname") as plugin_class:
            bad_nickname.PLUGIN_INFO = None
            bad_nickname.BOT = bot
            bad_nickname.KICK_REASON_MESSAGE = "x" * 41
            bad_nickname.start_plugin()
        plugin_class.assert_not_called()

        with patch.object(bad_nickname, "start_plugin") as start:
            bad_nickname.setup(bot, auto_start=True, kick_reason_message="reason")
        start.assert_called_once_with()

        with patch.object(bad_nickname, "start_plugin") as start:
            bad_nickname.setup(bot, auto_start=False, kick_reason_message="reason")
        start.assert_not_called()

    def test_parse_bad_name_patterns_groups_aliases(self):
        plugin = self._plugin()

        patterns = plugin.parse_bad_name_patterns(
            {
                "spam.name_pattern": "spam",
                "advertising.name_pattern": "advert",
            }
        )

        self.assertEqual(
            [pattern["regex_alias"] for pattern in patterns], ["spam", "advertising"]
        )
        self.assertTrue(patterns[0]["regex_object"].search("spam user"))

    def test_missing_patterns_are_reported_as_empty_configuration(self):
        plugin = self._plugin()

        self.assertEqual(plugin.parse_bad_name_patterns(None), [])

    def test_invalid_regex_is_reported_as_unusable(self):
        plugin = self._plugin()

        self.assertIsNone(plugin.compile_regex_pattern("["))
        plugin.logger.error.assert_called_once()

        self.assertEqual(
            plugin.parse_bad_name_patterns({"broken.name_pattern": "["}), []
        )

    def test_client_list_filters_serverquery_clients(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "0", "clid": "1"},
            {"client_type": "1", "clid": "2"},
        ]

        plugin.update_client_list()

        self.assertEqual(plugin.client_list, [{"client_type": "0", "clid": "1"}])

    def test_bad_nickname_list_matches_case_insensitively(self):
        plugin = self._plugin()
        plugin.client_list = [
            {"cid": "1", "client_type": "0", "client_nickname": "SpAmmer"},
            {"cid": "1", "client_type": "0", "client_nickname": "Regular"},
            {"cid": "1", "client_type": "1", "client_nickname": "SpAmQuery"},
        ]
        plugin.bad_name_patterns = [
            {"regex_alias": "spam", "regex_object": re.compile("spam")}
        ]

        result = plugin.get_client_list_with_bad_nickname()

        self.assertEqual(result, [plugin.client_list[0]])


if __name__ == "__main__":
    unittest.main()
