"""Shared lightweight TeamSpeak SDK stubs for unit tests."""

# pylint: disable=import-outside-toplevel,too-few-public-methods
import sys
import types
from unittest.mock import Mock


class TS3Exception(Exception):
    """Minimal SDK exception replacement for unit tests."""


class TS3QueryException(TS3Exception):
    """Minimal query exception replacement for unit tests."""


class _Registrar:
    """Minimal command/event registrar used while importing plugins."""

    def add_handler(self, *_args):
        """Ignore command registration during isolated plugin imports."""
        return None

    def add_observer(self, *_args):
        """Ignore event registration during isolated plugin imports."""
        return None


def install_ts3api_stubs():
    """Install the subset of ts3API modules imported by the bot code."""
    if getattr(sys.modules.get("ts3API"), "_unit_test_stub", False):
        return

    events = types.ModuleType("ts3API.Events")

    class _Event:
        """Small event stand-in matching the SDK constructor contract."""

        def __init__(self, data):
            self.data = data

    for name in (
        "TextMessageEvent",
        "ChannelEditedEvent",
        "ChannelDescriptionEditedEvent",
        "ClientEnteredEvent",
        "ClientLeftEvent",
        "ClientMovedEvent",
        "ClientMovedSelfEvent",
        "ServerEditedEvent",
        "ClientKickedEvent",
        "ClientBannedEvent",
    ):
        setattr(events, name, type(name, (_Event,), {}))

    connection = types.ModuleType("ts3API.TS3Connection")
    connection.TS3QueryException = TS3QueryException
    connection.TS3Connection = Mock
    utilities = types.ModuleType("ts3API.utilities")
    utilities.TS3Exception = TS3Exception
    utilities.TS3ConnectionClosedException = TS3Exception
    exception_types = types.ModuleType("ts3API.TS3QueryExceptionType")
    exception_types.TS3QueryExceptionType = types.SimpleNamespace(
        CLIENT_NICKNAME_INUSE="nickname", CHANNEL_ALREADY_IN="channel"
    )
    ts3api = types.ModuleType("ts3API")
    ts3api._unit_test_stub = True
    ts3api.TS3Connection = connection
    sys.modules.update(
        {
            "ts3API": ts3api,
            "ts3API.Events": events,
            "ts3API.TS3Connection": connection,
            "ts3API.utilities": utilities,
            "ts3API.TS3QueryExceptionType": exception_types,
        }
    )


def configure_module_loader():
    """Prepare decorators used by plugin modules during import."""
    import module_loader

    module_loader.COMMAND_HANDLER = _Registrar()
    module_loader.EVENT_HANDLER = _Registrar()
    return module_loader
