"""A small LRU cache with an optional time-to-live, used by the thumbnail loader."""

import time
from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity, ttl=None, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._data = OrderedDict()

    def _expired(self, stored_at):
        return self.ttl is not None and self.clock() - stored_at >= self.ttl

    def get(self, key, default=None):
        if key not in self._data:
            return default
        value, stored_at = self._data[key]
        if self._expired(stored_at):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def put(self, key, value):
        if key in self._data:
            self._data[key] = (value, self.clock())
            self._data.move_to_end(key)
            return
        if len(self._data) >= self.capacity:
            self._data.popitem(last=False)
        self._data[key] = (value, self.clock())

    def __contains__(self, key):
        return key in self._data and not self._expired(self._data[key][1])

    def __len__(self):
        return sum(1 for _, stored in self._data.values() if not self._expired(stored))
