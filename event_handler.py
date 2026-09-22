# standard imports
import logging

import threading
import time
import resource

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

    def __init__(
        self,
        ts3conn,
        command_handler,
        event_workers=2,
        command_workers=1,
        event_queue_size=50,
        command_queue_size=10,
        memory_limit_mb=128,
    ):
        self.ts3conn = ts3conn
        self.command_handler = command_handler
        self.observers = {}
        self._observers_by_event_type = {}
        self._observers_lock = threading.RLock()
        self._accepting_events = True
        self._queue_condition = threading.Condition()
        self._event_queue_size = int(event_queue_size)
        self._command_queue_size = int(command_queue_size)
        if self._event_queue_size < 1 or self._command_queue_size < 1:
            raise ValueError("Event and command queue sizes must be at least 1.")
        self._pending_event_jobs = 0
        self._pending_command_jobs = 0
        self._event_queue_warning_logged = False
        self._command_queue_warning_logged = False
        self._serverquery_client_ids = set()
        self._serverquery_client_ids_lock = threading.Lock()
        self._memory_limit_mb = int(memory_limit_mb)
        self._last_memory_check = 0.0
        # The executor queue is deliberately unbounded: TeamSpeak events are
        # state changes, so silently dropping them makes plugin state incorrect.
        self._executor = ThreadPoolExecutor(
            max_workers=int(event_workers), thread_name_prefix="ts3-event"
        )
        # Commands must not wait behind a burst of client events.
        self._command_executor = ThreadPoolExecutor(
            max_workers=int(command_workers), thread_name_prefix="ts3-command"
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
        event_class = type(evt)
        with self._observers_lock:
            cached_observers = self._observers_by_event_type.get(event_class)
            if cached_observers is not None:
                return cached_observers

            obs = set()
            for event_type in event_class.mro():
                obs.update(self.observers.get(event_type, set()))
            # Plugin observers are normally registered during startup. Caching the
            # complete MRO lookup avoids allocating a set for every incoming event.
            cached_observers = frozenset(obs)
            self._observers_by_event_type[event_class] = cached_observers
            return cached_observers

    def add_observer(self, obs, evt_type):
        """
        Add an observer for an event type.
        :param obs: Function to call upon a new event of type evt_type.
        :param evt_type: Event type to observe.
        :type evt_type: TS3Event
        """
        with self._observers_lock:
            obs_set = self.observers.get(evt_type, set())
            obs_set.add(obs)
            self.observers[evt_type] = obs_set
            self._observers_by_event_type.clear()

    def remove_observer(self, obs, evt_type):
        """
        Remove an observer for an event type.
        :param obs: Observer to remove.
        :param evt_type: Event type to remove the observer from.
        """
        with self._observers_lock:
            self.observers.get(evt_type, set()).discard(obs)
            self._observers_by_event_type.clear()

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
        is_command = isinstance(evt, TextMessageEvent)
        executor = self._command_executor if is_command else self._executor
        for observer in self.get_obs_for_event(evt):
            if not self._acquire_queue_slot(is_command):
                return
            try:
                executor.submit(self._inform_observer, observer, evt, is_command)
            except RuntimeError:
                self._release_queue_slot(is_command)
                EventHandler.logger.warning(
                    "Could not schedule event of type %s because the bot is stopping.",
                    type(evt),
                )
        self._warn_if_memory_limit_is_near()

    def _acquire_queue_slot(self, is_command):
        """Reserve a bounded queue slot, blocking without dropping work."""
        queue_name = "command" if is_command else "event"
        queue_size = self._command_queue_size if is_command else self._event_queue_size
        with self._queue_condition:
            pending_name = (
                "_pending_command_jobs" if is_command else "_pending_event_jobs"
            )
            warning_name = (
                "_command_queue_warning_logged"
                if is_command
                else "_event_queue_warning_logged"
            )
            while self._accepting_events and getattr(self, pending_name) >= queue_size:
                if not getattr(self, warning_name):
                    EventHandler.logger.warning(
                        "%s queue is full (%d jobs); processing is backpressured. "
                        "Consider increasing %sQueueSize or %sWorkers.",
                        queue_name.capitalize(),
                        queue_size,
                        queue_name.capitalize(),
                        queue_name.capitalize(),
                    )
                    setattr(self, warning_name, True)
                self._queue_condition.wait()
            if not self._accepting_events:
                return False
            setattr(self, pending_name, getattr(self, pending_name) + 1)
            return True

    def _release_queue_slot(self, is_command):
        with self._queue_condition:
            pending_name = (
                "_pending_command_jobs" if is_command else "_pending_event_jobs"
            )
            warning_name = (
                "_command_queue_warning_logged"
                if is_command
                else "_event_queue_warning_logged"
            )
            pending = getattr(self, pending_name) - 1
            setattr(self, pending_name, pending)
            queue_size = (
                self._command_queue_size if is_command else self._event_queue_size
            )
            if pending <= queue_size // 2:
                setattr(self, warning_name, False)
            self._queue_condition.notify_all()

    def _warn_if_memory_limit_is_near(self):
        """Warn at most once a minute when current RSS nears the limit."""
        if (
            self._memory_limit_mb <= 0
            or time.monotonic() - self._last_memory_check < 60
        ):
            return
        self._last_memory_check = time.monotonic()
        memory_mb = self._get_memory_usage_mb()
        if memory_mb >= self._memory_limit_mb * 0.8:
            EventHandler.logger.warning(
                "Current resident memory is %.1f MiB of configured %d MiB; "
                "consider increasing the bot memory limit or reducing load.",
                memory_mb,
                self._memory_limit_mb,
            )

    @staticmethod
    def _get_memory_usage_mb():
        """Return current Linux RSS, falling back to the portable peak metric."""
        try:
            with open("/proc/self/status", encoding="utf-8") as status_file:
                for line in status_file:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except (OSError, IndexError, ValueError):
            pass
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    def _inform_observer(self, observer, evt, is_command):
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
            self._release_queue_slot(is_command)

    def close(self):
        """Stop the observer executor during bot shutdown."""
        with self._queue_condition:
            self._accepting_events = False
            self._queue_condition.notify_all()
        self._executor.shutdown(wait=True)
        self._command_executor.shutdown(wait=True)
