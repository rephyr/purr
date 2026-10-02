"""A small LRU cache with an optional time-to-live, used by the thumbnail loader."""

import time
from collections import OrderedDict


class LRUCache:
    """Keeps at most `capacity` items. When it is full, the least recently used item goes.
    With a ttl, an item expires ttl seconds after it was last stored."""

    def __init__(self, capacity, ttl=None, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._data = OrderedDict()  # key -> (value, stored_at), oldest first

    def _expired(self, stored_at):
        return self.ttl is not None and self.clock() - stored_at > self.ttl

    def get(self, key, default=None):
        if key not in self._data:
            return default
        value, stored_at = self._data[key]
        if self._expired(stored_at):
            del self._data[key]
            return default
        return value

    def put(self, key, value):
        if key in self._data:
            self._data[key] = (value, self.clock())
            return
        if len(self._data) > self.capacity:
            self._data.popitem(last=False)
        self._data[key] = (value, self.clock())

    def __contains__(self, key):
        return key in self._data and not self._expired(self._data[key][1])

    def __len__(self):
        return len(self._data)
