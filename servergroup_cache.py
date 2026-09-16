"""Thread-safe cache for TeamSpeak server-group metadata."""

from threading import Lock
from time import monotonic
from weakref import WeakKeyDictionary

# Server-group definitions rarely change. Refreshing them periodically keeps the
# cache accurate while avoiding one ServerQuery command for every ClientInfo.
REFRESH_INTERVAL_SECONDS = 300.0

_caches = WeakKeyDictionary()
_caches_lock = Lock()


class ServerGroupCache:
    """Cache the server-group list for one TeamSpeak connection."""

    def __init__(self, connection):
        self._connection = connection
        self._groups = ()
        self._refreshed_at = None
        self._lock = Lock()

    def get(self):
        """Return cached groups, refreshing at most once per configured interval."""
        now = monotonic()
        if (
            self._refreshed_at is not None
            and now - self._refreshed_at < REFRESH_INTERVAL_SECONDS
        ):
            return self._groups

        with self._lock:
            now = monotonic()
            if (
                self._refreshed_at is None
                or now - self._refreshed_at >= REFRESH_INTERVAL_SECONDS
            ):
                # Make the outer collection immutable so callers cannot mutate
                # cached state. Individual dictionaries are only read by callers.
                self._groups = tuple(self._connection.servergrouplist())
                self._refreshed_at = now
            return self._groups

    def invalidate(self):
        """Cause the next lookup to refresh the group definitions."""
        with self._lock:
            self._refreshed_at = None


def get_servergroups(connection):
    """Get server groups from the cache belonging to *connection*."""
    with _caches_lock:
        cache = _caches.get(connection)
        if cache is None:
            cache = ServerGroupCache(connection)
            _caches[connection] = cache
    return cache.get()


def invalidate_servergroups(connection):
    """Invalidate cached server groups for a connection, if present."""
    with _caches_lock:
        cache = _caches.get(connection)
    if cache is not None:
        cache.invalidate()
