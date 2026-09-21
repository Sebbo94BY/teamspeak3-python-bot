# standard imports
import logging

import threading

# pylint: disable=consider-using-with
from concurrent.futures import ThreadPoolExecutor

# third-party imports
from ts3API.Events import (
    TextMessageEvent,
    ChannelEditedEvent,
    ChannelDescriptionEditedEvent,
    ClientEnteredEvent,
    ClientLeftEvent,
    ClientMovedEvent,
    ClientMovedSelfEvent,
    ServerEditedEvent,
)

# local imports
from log_utils import create_log_handler


class EventHandler:
    """
    EventHandler class responsible for delegating events to registered listeners.
    """

    # configure logger
    class_name = __qualname__
    logger = logging.getLogger(class_name)
    logger.propagate = 0
    logger.setLevel(logging.INFO)
    file_handler = create_log_handler(f"logs/{class_name.lower()}.log")
    formatter = logging.Formatter("%(asctime)s: %(levelname)s: %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.info("Configured %s logger", str(class_name))
    logger.propagate = 0

    def __init__(self, ts3conn, command_handler):
        self.ts3conn = ts3conn
        self.command_handler = command_handler
        self.observers = {}
        self._pending_observers = threading.BoundedSemaphore(100)
        self._coalesced_observer_keys = set()
        self._coalesced_observer_keys_lock = threading.Lock()
        self._serverquery_client_ids = set()
        self._serverquery_client_ids_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="ts3-event"
        )
        self.add_observer(self.command_handler.inform, TextMessageEvent)

    def on_event(self, _sender, **kw):
        """
        Called upon a new event. Logs the event and informs all listeners.
        """
        # parsed_event = Events.EventParser.parse_event(event=event)
        parsed_event = kw["event"]
        if self._is_serverquery_event(parsed_event):
            return
        if isinstance(parsed_event, TextMessageEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ChannelEditedEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ChannelDescriptionEditedEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ClientEnteredEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ClientLeftEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ClientMovedEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ClientMovedSelfEvent):
            logging.debug(type(parsed_event))
        elif isinstance(parsed_event, ServerEditedEvent):
            logging.debug("Event of type %s", type(parsed_event))
            logging.debug(parsed_event.changed_properties)

        # Inform all observers
        self.inform_all(parsed_event)

    @staticmethod
    def _get_event_client_id(evt):
        """Return an event's client ID, if the TeamSpeak event provides one."""
        client_id = getattr(evt, "client_id", None)
        if client_id is not None:
            return str(client_id)
        return str(getattr(evt, "data", {}).get("clid", "")) or None

    def _is_serverquery_event(self, evt):
        """Track and suppress all events belonging to ServerQuery clients.

        ServerQuery connections have ``client_type=1`` on their entered event.
        Their later move/leave events omit that field, so remember their IDs for
        the lifetime of the connection instead of issuing another query.
        """
        client_id = self._get_event_client_id(evt)
        if isinstance(evt, ClientEnteredEvent):
            is_serverquery = str(getattr(evt, "client_type", "")) == "1"
            if client_id is not None:
                with self._serverquery_client_ids_lock:
                    if is_serverquery:
                        self._serverquery_client_ids.add(client_id)
                    else:
                        # TeamSpeak can reuse a client ID after the old client left.
                        self._serverquery_client_ids.discard(client_id)
            return is_serverquery

        if client_id is None:
            return False

        with self._serverquery_client_ids_lock:
            is_serverquery = client_id in self._serverquery_client_ids
            if isinstance(evt, ClientLeftEvent):
                self._serverquery_client_ids.discard(client_id)
        return is_serverquery

    def get_obs_for_event(self, evt):
        """
        Get all observers for an event.
        :param evt: Event to get observers for.
        :return: List of observers.
        :rtype: list[function]
        """
        obs = set()
        for event_type in type(evt).mro():
            obs.update(self.observers.get(event_type, set()))
        return obs

    def add_observer(self, obs, evt_type):
        """
        Add an observer for an event type.
        :param obs: Function to call upon a new event of type evt_type.
        :param evt_type: Event type to observe.
        :type evt_type: TS3Event
        """
        obs_set = self.observers.get(evt_type, set())
        obs_set.add(obs)
        self.observers[evt_type] = obs_set

    def remove_observer(self, obs, evt_type):
        """
        Remove an observer for an event type.
        :param obs: Observer to remove.
        :param evt_type: Event type to remove the observer from.
        """
        self.observers.get(evt_type, set()).discard(obs)

    def remove_observer_from_all(self, obs):
        """
        Removes an observer from all event_types.
        :param obs: Observer to remove.
        """
        for evt_type in self.observers:
            self.remove_observer(obs, evt_type)

    # We really want to catch all exception here, to prevent one observer from crashing the bot
    # noinspection PyBroadException
    def inform_all(self, evt):
        """
        Inform all observers registered to the event type of an event.
        :param evt: Event to inform observers of.
        """
        for observer in self.get_obs_for_event(evt):
            coalesced_key = self._get_coalesced_key(observer, evt)
            if coalesced_key is not None:
                with self._coalesced_observer_keys_lock:
                    if coalesced_key in self._coalesced_observer_keys:
                        continue
                    self._coalesced_observer_keys.add(coalesced_key)
            if not self._pending_observers.acquire(blocking=False):
                self._discard_coalesced_key(coalesced_key)
                EventHandler.logger.warning(
                    "Dropping event of type %s because the observer queue is full.",
                    str(type(evt)),
                )
                continue
            try:
                self._executor.submit(
                    self._inform_observer, observer, evt, coalesced_key
                )
            except RuntimeError:
                self._discard_coalesced_key(coalesced_key)
                self._pending_observers.release()

    @staticmethod
    def _get_coalesced_key(observer, evt):
        """Return a queue-deduplication key requested by an observer, if any."""
        scope = getattr(observer, "event_coalesce_scope", None)
        if scope == "global":
            return observer
        if scope == "client":
            client_id = getattr(evt, "client_id", None)
            if client_id is not None:
                return observer, client_id
        return None

    def _discard_coalesced_key(self, coalesced_key):
        if coalesced_key is not None:
            with self._coalesced_observer_keys_lock:
                self._coalesced_observer_keys.discard(coalesced_key)

    def _inform_observer(self, observer, evt, coalesced_key=None):
        """Run an observer while retaining its exceptions in the bot log."""
        try:
            observer(evt)
        except BaseException:
            EventHandler.logger.exception(
                "Exception while informing %s of Event of type %s\nOriginal data: %s",
                str(observer),
                str(type(evt)),
                str(evt.data),
            )
        finally:
            self._discard_coalesced_key(coalesced_key)
            self._pending_observers.release()

    def close(self):
        """Stop the observer executor during bot shutdown."""
        self._executor.shutdown(wait=True)
