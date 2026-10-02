"""Unit tests for the Twitch Live plugin."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from tests.support import (
    TS3Exception,
    TS3QueryException,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.twitch_live import main as twitch_live


class TwitchLiveTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = twitch_live.TwitchLive.__new__(twitch_live.TwitchLive)
        plugin.twitch_user_ids = {}
        plugin.twitch_api_access_token = "token"
        plugin.twitch_api_client_id = "client"
        plugin.ts3conn = Mock()
        plugin.logger = Mock()
        return plugin

    @staticmethod
    def _query_error():
        return TS3QueryException()

    def test_servergroup_lookup_and_token_cache(self):
        plugin = self._plugin()
        plugin.ts3conn.find_servergroup_by_name.return_value = {"sgid": "9"}
        self.assertEqual(plugin.get_servergroup_by_name("Live"), "9")
        plugin.ts3conn.find_servergroup_by_name.side_effect = TS3Exception()
        with self.assertRaises(TS3Exception):
            plugin.get_servergroup_by_name("Live")

        plugin.twitch_api_expires_at = datetime.now() + timedelta(minutes=10)
        plugin.get_oauth_access_token()
        plugin.logger.debug.assert_called()

    def test_oauth_success_sets_token_and_expiration(self):
        plugin = self._plugin()
        plugin.twitch_api_expires_at = None
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with (
            patch.object(
                twitch_live.request, "urlopen", return_value=response
            ) as urlopen,
            patch.object(
                twitch_live.json,
                "load",
                return_value={"access_token": "new-token", "expires_in": 3600},
            ),
        ):
            plugin.get_oauth_access_token()
        self.assertEqual(plugin.twitch_api_access_token, "new-token")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10.0)

    def test_client_list_and_descriptions_handle_errors_and_empty_values(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.return_value = [
            {"client_type": "1", "clid": "1"},
            {"client_type": "0", "clid": "2", "client_nickname": "Ada"},
        ]
        self.assertEqual(
            plugin.get_client_list(), [plugin.ts3conn.clientlist.return_value[1]]
        )
        plugin.ts3conn.clientlist.side_effect = TS3Exception()
        self.assertEqual(plugin.get_client_list(), [])

        plugin.get_client_list = Mock(
            return_value=[
                {"clid": "2", "client_database_id": "7", "client_nickname": "Ada"},
                {"clid": "3", "client_database_id": "8", "client_nickname": "Lin"},
            ]
        )
        with patch.object(
            twitch_live.client_info,
            "ClientInfo",
            side_effect=[
                Mock(description="ada", servergroup_ids=("1",)),
                Mock(description="", servergroup_ids=()),
            ],
        ):
            self.assertEqual(
                plugin.get_clients_with_a_description(),
                [
                    {
                        "clid": "2",
                        "client_database_id": "7",
                        "client_description": "ada",
                        "servergroup_ids": ("1",),
                    }
                ],
            )

    def test_single_user_lookup_delegates_to_batched_lookup(self):
        plugin = self._plugin()
        plugin.get_twitch_streamer_user_ids = Mock(return_value={"ada": "1"})
        self.assertEqual(plugin.get_twitch_streamer_user_id("ada"), "1")
        plugin.get_twitch_streamer_user_ids.assert_called_once_with(["ada"])

    def test_stream_lookup_handles_empty_batches_and_network_errors(self):
        plugin = self._plugin()
        self.assertEqual(plugin.get_live_streamer_user_ids([]), set())
        with patch.object(
            twitch_live.request,
            "urlopen",
            side_effect=twitch_live.error.HTTPError("url", 500, "error", {}, None),
        ):
            with self.assertRaises(twitch_live.error.HTTPError):
                plugin.get_live_streamer_user_ids(["1"])

    def test_servergroup_membership_lookup_handles_success_and_errors(self):
        plugin = self._plugin()
        plugin.ts3conn._parse_resp_to_list_of_dicts.return_value = [{"sgid": "2"}]
        self.assertEqual(plugin.get_servergroup_ids_by_client("7"), ["2"])
        plugin.ts3conn._send.side_effect = self._query_error()
        self.assertEqual(plugin.get_servergroup_ids_by_client("7"), [])

    def test_manage_live_status_queries_status_and_avoids_redundant_changes(self):
        plugin = self._plugin()
        plugin.live_servergroup_id = "9"
        plugin.get_live_streamer_user_ids = Mock(return_value={"1"})
        plugin.get_servergroup_ids_by_client = Mock(return_value=[])
        client = {"twitch_user_id": "1", "client_database_id": "42"}
        plugin.manage_live_status(client)
        plugin.ts3conn._send.assert_called_once()

        plugin.ts3conn._send.reset_mock()
        client["servergroup_ids"] = ["9"]
        plugin.manage_live_status(client, twitch_stream_online=True)
        plugin.ts3conn._send.assert_not_called()

        plugin.manage_live_status(
            {"twitch_user_id": "1", "client_database_id": "42", "servergroup_ids": []},
            twitch_stream_online=False,
        )
        plugin.ts3conn._send.assert_not_called()

    def test_manage_live_status_propagates_servergroup_errors(self):
        plugin = self._plugin()
        plugin.live_servergroup_id = "9"
        plugin.ts3conn._send.side_effect = TS3Exception()
        with self.assertRaises(TS3Exception):
            plugin.manage_live_status(
                {"client_database_id": "42", "servergroup_ids": []}, True
            )
        with self.assertRaises(TS3Exception):
            plugin.manage_live_status(
                {"client_database_id": "42", "servergroup_ids": ["9"]}, False
            )

    def test_loop_processes_clients_and_catches_iteration_errors(self):
        plugin = self._plugin()
        plugin.stopped = Mock()
        plugin.stopped.wait.side_effect = [False, True]
        plugin.get_oauth_access_token = Mock()
        plugin.get_clients_with_a_description = Mock(
            return_value=[
                {"client_description": "ada", "twitch_user_id": None},
            ]
        )
        plugin.get_twitch_streamer_user_ids = Mock(return_value={"ada": "1"})
        plugin.get_live_streamer_user_ids = Mock(return_value={"1"})
        plugin.manage_live_status = Mock()
        plugin.loop_until_stopped()
        plugin.manage_live_status.assert_called_once()

        plugin.stopped.wait.side_effect = [False, True]
        plugin.get_oauth_access_token.side_effect = RuntimeError("broken")
        plugin.loop_until_stopped()

    def test_setup_validates_timeout_and_lifecycle(self):
        bot = type("Bot", (), {"ts3conn": Mock()})()
        with self.assertRaises(ValueError):
            twitch_live.setup(bot, auto_start=False, http_timeout=0)
        with patch.object(twitch_live, "start_plugin") as start:
            twitch_live.setup(
                bot,
                auto_start=True,
                enable_dry_run=True,
                frequency=3,
                http_timeout=4,
                twitch_live_servergroup_name="Live",
                twitch_api_client_id="id",
                twitch_api_client_secret="secret",
            )
        start.assert_called_once_with()
        self.assertTrue(twitch_live.DRY_RUN)

    def test_twitch_login_normalizes_urls_and_rejects_whitespace(self):
        self.assertEqual(
            twitch_live.TwitchLive._twitch_login(" https://www.twitch.tv/Ada "),
            "ada",
        )
        self.assertIsNone(twitch_live.TwitchLive._twitch_login("ada streamer"))
        self.assertIsNone(twitch_live.TwitchLive._twitch_login(""))

    def test_requests_have_a_timeout(self):
        plugin = self._plugin()
        plugin.twitch_api_expires_at = None

        with patch.object(
            twitch_live.request, "urlopen", side_effect=TimeoutError
        ) as urlopen:
            with self.assertRaises(TimeoutError):
                plugin.get_oauth_access_token()

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10.0)

    def test_user_ids_are_batched_and_cached(self):
        plugin = self._plugin()

        with (
            patch.object(twitch_live.request, "urlopen") as urlopen,
            patch.object(
                twitch_live.json,
                "load",
                return_value={"data": [{"login": "ada", "id": "1"}]},
            ),
        ):
            self.assertEqual(
                plugin.get_twitch_streamer_user_ids(
                    ["https://www.twitch.tv/Ada", "ada"]
                ),
                {"https://www.twitch.tv/Ada": "1", "ada": "1"},
            )
            plugin.get_twitch_streamer_user_ids(["ada"])

        urlopen.assert_called_once()

    def test_stream_statuses_are_batched(self):
        plugin = self._plugin()

        with (
            patch.object(twitch_live.request, "urlopen") as urlopen,
            patch.object(
                twitch_live.json,
                "load",
                return_value={"data": [{"user_id": "1"}]},
            ),
        ):
            self.assertEqual(plugin.get_live_streamer_user_ids(["1", "2"]), {"1"})

        urlopen.assert_called_once()

    def test_invalid_descriptions_do_not_make_requests(self):
        plugin = self._plugin()

        with patch.object(twitch_live.request, "urlopen") as urlopen:
            self.assertEqual(
                plugin.get_twitch_streamer_user_ids(["", "not a twitch login"]),
                {},
            )

        urlopen.assert_not_called()

    def test_user_id_lookups_respect_the_batch_limit(self):
        plugin = self._plugin()
        descriptions = [f"streamer{index}" for index in range(101)]

        with (
            patch.object(twitch_live.request, "urlopen") as urlopen,
            patch.object(twitch_live.json, "load", return_value={"data": []}),
        ):
            result = plugin.get_twitch_streamer_user_ids(descriptions)

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(result, {description: None for description in descriptions})

    def test_user_id_lookup_propagates_network_errors_without_caching(self):
        plugin = self._plugin()

        with patch.object(
            twitch_live.request,
            "urlopen",
            side_effect=twitch_live.error.URLError("down"),
        ):
            with self.assertRaises(twitch_live.error.URLError):
                plugin.get_twitch_streamer_user_ids(["ada"])

        self.assertEqual(plugin.twitch_user_ids, {})

    def test_online_stream_assigns_the_live_servergroup(self):
        plugin = self._plugin()
        plugin.live_servergroup_id = "9"
        client = {
            "client_database_id": "42",
            "servergroup_ids": [],
        }

        with patch.object(twitch_live, "DRY_RUN", False):
            plugin.manage_live_status(client, twitch_stream_online=True)

        plugin.ts3conn._send.assert_called_once_with(
            "servergroupaddclient", ["sgid=9", "cldbid=42"]
        )

    def test_offline_stream_removes_the_live_servergroup(self):
        plugin = self._plugin()
        plugin.live_servergroup_id = "9"
        client = {
            "client_database_id": "42",
            "servergroup_ids": ["9"],
        }

        with patch.object(twitch_live, "DRY_RUN", False):
            plugin.manage_live_status(client, twitch_stream_online=False)

        plugin.ts3conn._send.assert_called_once_with(
            "servergroupdelclient", ["sgid=9", "cldbid=42"]
        )

    def test_dry_run_does_not_change_live_servergroups(self):
        plugin = self._plugin()
        plugin.live_servergroup_id = "9"
        client = {
            "client_database_id": "42",
            "servergroup_ids": [],
        }

        with patch.object(twitch_live, "DRY_RUN", True):
            plugin.manage_live_status(client, twitch_stream_online=True)

        plugin.ts3conn._send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
