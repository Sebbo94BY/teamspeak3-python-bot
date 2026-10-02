"""Unit tests for the plugin template."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import (
    TS3Exception,
    configure_module_loader,
    install_ts3api_stubs,
)

install_ts3api_stubs()
configure_module_loader()

from modules.plugin_template import main as plugin_template


class PluginTemplateTests(unittest.TestCase):
    @staticmethod
    def _plugin():
        plugin = plugin_template.PluginTemplate.__new__(plugin_template.PluginTemplate)
        plugin.logger = Mock()
        plugin.ts3conn = Mock()
        return plugin

    def test_constructor_and_empty_broadcast_are_safe(self):
        plugin = plugin_template.PluginTemplate(Mock(), Mock())
        self.assertIsNone(plugin.client_list)
        plugin.logger = Mock()
        plugin.send_message_to_all_clients()
        plugin.logger.debug.assert_called_once()

    def test_broadcast_supports_dry_run_and_sdk_errors(self):
        plugin = self._plugin()
        plugin.client_list = [{"clid": "1", "client_type": "0"}]
        plugin_template.BOT = SimpleNamespace(ts3conn=Mock())
        with patch.object(plugin_template, "DRY_RUN", True):
            plugin.send_message_to_all_clients()
        with (
            patch.object(plugin_template, "DRY_RUN", False),
            patch.object(
                plugin_template.teamspeak_bot,
                "send_msg_to_client",
                side_effect=TS3Exception(),
            ),
        ):
            plugin.send_message_to_all_clients()

    def test_loop_and_run_process_work_and_catch_failures(self):
        plugin = self._plugin()
        plugin.stopped = Mock()
        plugin.stopped.wait.side_effect = [False, True]
        plugin.update_client_list = Mock()
        plugin.send_message_to_all_clients = Mock()
        plugin.loop_until_stopped()
        plugin.update_client_list.assert_called_once()
        plugin.send_message_to_all_clients.assert_called_once()

        plugin.stopped.wait.side_effect = [False, True]
        plugin.update_client_list.side_effect = RuntimeError("broken")
        plugin.loop_until_stopped()

        plugin.loop_until_stopped = Mock(side_effect=RuntimeError("broken"))
        plugin.run()
        plugin.logger.exception.assert_called()

    def test_commands_events_and_setup_delegate(self):
        bot = SimpleNamespace(ts3conn=Mock())
        with patch.object(plugin_template.teamspeak_bot, "send_msg_to_client") as send:
            plugin_template.BOT = bot
            plugin_template.send_version(7)
        send.assert_called_once()

        with patch.object(plugin_template, "start_plugin") as start:
            plugin_template.setup(
                bot, auto_start=True, enable_dry_run=True, frequency=2
            )
        start.assert_called_once_with()

        plugin = self._plugin()
        plugin.join = Mock()
        with patch.object(plugin_template, "PLUGIN_INFO", plugin):
            plugin_template.client_left_event(SimpleNamespace(client_id=7))
            plugin_template.stop_plugin()
        plugin.join.assert_called_once()

    def test_update_client_list_handles_team_speak_errors(self):
        plugin = self._plugin()
        plugin.ts3conn.clientlist.side_effect = plugin_template.TS3Exception("down")

        plugin.update_client_list()

        self.assertEqual(plugin.client_list, [])

    def test_broadcast_skips_serverquery_clients(self):
        plugin = self._plugin()
        plugin.client_list = [
            {"clid": "1", "client_type": "0"},
            {"clid": "2", "client_type": "1"},
        ]
        plugin_template.BOT = SimpleNamespace(ts3conn=Mock())

        with patch.object(plugin_template.teamspeak_bot, "send_msg_to_client") as send:
            plugin.send_message_to_all_clients()

        send.assert_called_once_with(plugin_template.BOT.ts3conn, "1", "Hello World!")

    def test_broadcast_ignores_missing_client_fields(self):
        plugin = self._plugin()
        plugin.client_list = [{"client_type": "0"}, {"clid": "2"}]

        with patch.object(plugin_template.teamspeak_bot, "send_msg_to_client") as send:
            plugin.send_message_to_all_clients()

        send.assert_not_called()
        self.assertEqual(plugin.logger.error.call_count, 2)


if __name__ == "__main__":
    unittest.main()
