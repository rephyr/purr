"""The pet: care, coins and errands over time."""

import json
from pathlib import Path

from . import care

DATA = json.loads((Path(__file__).parent / "data.json").read_text())


class Pet:
    def __init__(self, food=100.0, mood=100.0, coin_boost=1.0, data=None):
        self.data = data or DATA
        self.food, self.mood = float(food), float(mood)
        self.coin_boost = coin_boost
        self.coins = 0
        self.carry = 0.0
        self.clock = 0.0
        self.errands = []
        self.errand_time = 0.0  # how long the first errand has been running
        self.is_open = True

    # ---- care ----

    def feed(self, amount):
        self.food = min(100.0, self.food + amount)

    def play(self, amount):
        self.mood = min(100.0, self.mood + amount)

    def add_errand(self, errand_id):
        if errand_id not in self.data["errands"]:
            raise KeyError(errand_id)
        self.errands.append(errand_id)

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    # ---- time ----

    def _earn(self, amount):
        self.carry += amount
        whole = int(self.carry)
        self.coins += whole
        self.carry -= whole

    def advance(self, seconds):
        """Move time on by `seconds` and return what happened."""
        if not self.is_open or seconds <= 0:
            return []
        events = []
        d = self.data
        before = care.active_buffs(d, self.food, self.mood)
        rate = d["income_per_second"] * care.multiplier(d, self.food, self.mood, self.coin_boost)
        self._earn(rate * seconds)
        self.food = care.drain(self.food, d["drain_per_second"]["food"], seconds, d["floor"])
        self.mood = care.drain(self.mood, d["drain_per_second"]["mood"], seconds, d["floor"])
        end = self.clock + seconds
        after = care.active_buffs(d, self.food, self.mood)
        for b in before:
            if b not in after:
                events.append(("buff_off", b, end))
        if self.errands:
            self.errand_time += seconds
            first = d["errands"][self.errands[0]]
            if self.errand_time >= first["seconds"]:
                self.errand_time = 0.0
                done = self.errands.pop(0)
                self._earn(first["reward"] * care.multiplier(d, self.food, self.mood, self.coin_boost))
                events.append(("errand_done", done, end))
        self.clock = end
        return events

    # ---- saving ----

    def save(self):
        return {"food": self.food, "mood": self.mood, "coin_boost": self.coin_boost,
                "coins": self.coins, "clock": self.clock, "errands": list(self.errands),
                "is_open": self.is_open}

    @classmethod
    def load(cls, saved, data=None):
        pet = cls(saved["food"], saved["mood"], saved.get("coin_boost", 1.0), data)
        pet.coins = saved["coins"]
        pet.clock = saved["clock"]
        pet.errands = list(saved["errands"])
        pet.is_open = saved.get("is_open", True)
        return pet
