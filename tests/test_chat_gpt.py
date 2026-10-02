"""Unit tests for the ChatGPT plugin's OpenAI integration."""

# pylint: disable=missing-class-docstring,missing-function-docstring,wrong-import-position
import importlib
import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, create_autospec, patch

from tests.support import (
    TS3Exception,
    configure_module_loader,
    install_ts3api_stubs,
)

os.makedirs("logs", exist_ok=True)
install_ts3api_stubs()
configure_module_loader()


class ChatGPTTests(unittest.TestCase):
    @staticmethod
    def _load_chat_gpt_module():
        """Load the ChatGPT plugin when its production dependency is installed."""
        if importlib.util.find_spec("openai") is None:
            raise unittest.SkipTest("openai is not installed")
        return importlib.import_module("modules.chat_gpt.main")

    def test_imports_the_openai_sdk_contract(self):
        chat_gpt = self._load_chat_gpt_module()
        client = chat_gpt.OpenAI(api_key="test-key")

        self.assertTrue(callable(chat_gpt.OpenAI))
        self.assertTrue(issubclass(chat_gpt.AuthenticationError, Exception))
        self.assertTrue(callable(client.responses.create))

    def test_uses_responses_api_without_network(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        sdk_client = chat_gpt.OpenAI(api_key="test-key")
        client = Mock()
        client.responses.create = create_autospec(
            sdk_client.responses.create,
            return_value=SimpleNamespace(output_text="  A response.  "),
        )
        plugin_info = SimpleNamespace(
            openai_api_key="test-key", openai_model="test-model"
        )

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client) as openai_client,
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask Explain testing")

        openai_client.assert_called_once_with(api_key="test-key")
        client.responses.create.assert_called_once_with(
            model="test-model",
            input="Explain testing",
            reasoning={"effort": "medium"},
        )
        self.assertEqual(
            send.call_args_list,
            [
                call(connection, 42, "I'm on it. Give me a few seconds..."),
                call(connection, 42, "A response."),
            ],
        )

    def test_handles_authentication_errors_without_network(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        client = Mock()
        auth_error = chat_gpt.AuthenticationError.__new__(chat_gpt.AuthenticationError)
        Exception.__init__(auth_error, "invalid API key")
        client.responses.create.side_effect = auth_error
        plugin_info = SimpleNamespace(
            openai_api_key="test-key", openai_model="test-model"
        )

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client),
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask Explain testing")

        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            send.call_args_list[-1],
            call(
                connection,
                42,
                "The authentication to the ChatGPT server failed. Please inform your administrator to check this issue.",
            ),
        )

    def test_handles_missing_response_text(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        client = Mock()
        client.responses.create.return_value = SimpleNamespace()
        plugin_info = SimpleNamespace(
            openai_api_key="test-key", openai_model="test-model"
        )

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client),
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask Explain testing")

        self.assertEqual(
            send.call_args_list[-1],
            call(
                connection,
                42,
                "ChatGPT did not return any response. Please try again. Maybe with a different wording.",
            ),
        )

    def test_handles_empty_response_text(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="   ")
        plugin_info = SimpleNamespace(
            openai_api_key="test-key", openai_model="test-model"
        )

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client),
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask Explain testing")

        self.assertEqual(
            send.call_args_list[-1],
            call(
                connection,
                42,
                "ChatGPT did not return any response. Please try again. Maybe with a different wording.",
            ),
        )

    def test_handles_unexpected_openai_errors_without_leaking_to_event_handler(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        client = Mock()
        client.responses.create.side_effect = RuntimeError("service unavailable")
        plugin_info = SimpleNamespace(
            openai_api_key="test-key", openai_model="test-model"
        )

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client),
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask Explain testing")

        self.assertEqual(
            send.call_args_list[-1],
            call(
                connection, 42, "The request to ChatGPT failed. Please try again later."
            ),
        )

    def test_setup_can_configure_plugin_without_starting_a_thread(self):
        chat_gpt = self._load_chat_gpt_module()
        original_values = (
            getattr(chat_gpt, "BOT", None),
            chat_gpt.AUTO_START,
            chat_gpt.DRY_RUN,
            chat_gpt.OPENAI_API_KEY,
            chat_gpt.OPENAI_MODEL,
        )
        bot = SimpleNamespace(ts3conn=Mock())

        try:
            with patch.object(chat_gpt, "start_plugin") as start:
                chat_gpt.setup(
                    bot,
                    auto_start=False,
                    enable_dry_run=True,
                    openai_api_key="test-key",
                    openai_model="test-model",
                )

            start.assert_not_called()
            self.assertIs(chat_gpt.BOT, bot)
            self.assertEqual(chat_gpt.OPENAI_API_KEY, "test-key")
            self.assertEqual(chat_gpt.OPENAI_MODEL, "test-model")
            self.assertTrue(chat_gpt.DRY_RUN)
        finally:
            (
                chat_gpt.BOT,
                chat_gpt.AUTO_START,
                chat_gpt.DRY_RUN,
                chat_gpt.OPENAI_API_KEY,
                chat_gpt.OPENAI_MODEL,
            ) = original_values

    def test_splits_long_responses_for_teamspeak(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="x" * 1024)
        plugin_info = SimpleNamespace(
            openai_api_key="test-key", openai_model="test-model"
        )

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client),
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask Explain testing")

        self.assertEqual(
            send.call_args_list[1:],
            [
                call(connection, 42, "x" * 1023),
                call(connection, 42, "x"),
            ],
        )

    def test_plugin_instance_and_lifecycle_are_configured(self):
        chat_gpt = self._load_chat_gpt_module()
        with (
            patch.object(chat_gpt, "OPENAI_API_KEY", "key"),
            patch.object(chat_gpt, "OPENAI_MODEL", "model"),
        ):
            plugin = chat_gpt.ChatGPT(Mock(), Mock())
        self.assertEqual(plugin.openai_api_key, "key")
        self.assertEqual(plugin.openai_model, "model")

        bot = SimpleNamespace(ts3conn=Mock())
        with patch.object(chat_gpt, "start_plugin") as start:
            chat_gpt.setup(
                bot, auto_start=True, openai_api_key="key", openai_model="model"
            )
        start.assert_called_once_with()

        plugin_info = Mock()
        plugin_info.join = Mock()
        with patch.object(chat_gpt, "PLUGIN_INFO", plugin_info):
            chat_gpt.stop_plugin()
        plugin_info.join.assert_called_once()

    def test_message_send_failures_are_logged_without_breaking_command(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        plugin_info = SimpleNamespace(openai_api_key="key", openai_model="model")
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="answer")
        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "PLUGIN_INFO", plugin_info),
            patch.object(chat_gpt, "OpenAI", return_value=client),
            patch.object(
                chat_gpt.teamspeak_bot,
                "send_msg_to_client",
                side_effect=TS3Exception(),
            ),
            patch.object(chat_gpt.ChatGPT, "logger") as logger,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask test")
        logger.exception.assert_called()

    def test_missing_prompt_message_failure_and_version_are_safe(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()
        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(
                chat_gpt.teamspeak_bot,
                "send_msg_to_client",
                side_effect=TS3Exception(),
            ),
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask")
            chat_gpt.send_version(sender=42)

    def test_does_not_call_openai_for_an_empty_prompt(self):
        chat_gpt = self._load_chat_gpt_module()
        connection = Mock()

        with (
            patch.object(
                chat_gpt, "BOT", SimpleNamespace(ts3conn=connection), create=True
            ),
            patch.object(chat_gpt, "OpenAI") as openai_client,
            patch.object(chat_gpt.teamspeak_bot, "send_msg_to_client") as send,
        ):
            chat_gpt.ask_chatgpt(sender=42, msg="!chatgpt ask   ")

        openai_client.assert_not_called()
        send.assert_called_once_with(
            connection,
            42,
            "You need to provide some text to ChatGPT, so that it can react to it.",
        )
