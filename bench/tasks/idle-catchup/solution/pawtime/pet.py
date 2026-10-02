"""The pet: care, coins and errands over time.

Everything is kept as exact Fractions, and advance() walks from one moment where something
changes (a buff turns off, an errand finishes) to the next, so one big step and many small ones
come out exactly the same: floats would lose crumbs that add up to whole coins.
"""

import json
from fractions import Fraction
from pathlib import Path

from . import care

DATA = json.loads((Path(__file__).parent / "data.json").read_text())


def exact(x):
    """A number (or a saved "n/d" string) as an exact Fraction: a float's exact binary value."""
    return Fraction(x)


def _exact_data(data):
    return {
        "floor": exact(data["floor"]),
        "drain_per_second": {k: exact(v) for k, v in data["drain_per_second"].items()},
        "buffs": [{**b, "above": exact(b["above"]), "x": exact(b["x"])} for b in data["buffs"]],
        "income_per_second": exact(data["income_per_second"]),
        "errands": {k: {"seconds": exact(v["seconds"]), "reward": exact(v["reward"])}
                    for k, v in data["errands"].items()},
    }


class Pet:
    def __init__(self, food=100.0, mood=100.0, coin_boost=1.0, data=None):
        self.data = data or DATA
        self._d = _exact_data(self.data)
        self._food, self._mood = exact(food), exact(mood)
        self._boost = exact(coin_boost)
        self._money = Fraction(0)  # every coin and part of a coin earned
        self._clock = Fraction(0)
        self._errand_time = Fraction(0)
        self.errands = []
        self.is_open = True

    # what the game reads: plain numbers
    food = property(lambda self: float(self._food))
    mood = property(lambda self: float(self._mood))
    coin_boost = property(lambda self: float(self._boost))
    clock = property(lambda self: float(self._clock))
    errand_time = property(lambda self: float(self._errand_time))
    coins = property(lambda self: int(self._money))  # floor: money is never negative
    carry = property(lambda self: float(self._money - int(self._money)))

    # ---- care ----

    def feed(self, amount):
        self._food = min(Fraction(100), self._food + exact(amount))

    def play(self, amount):
        self._mood = min(Fraction(100), self._mood + exact(amount))

    def add_errand(self, errand_id):
        if errand_id not in self.data["errands"]:
            raise KeyError(errand_id)
        self.errands.append(errand_id)

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    # ---- time ----

    def _multiplier(self):
        return care.multiplier(self._d, self._food, self._mood, self._boost)

    def _until_next(self):
        """Seconds until something changes: the first buff that's on reaching its line, or the
        running errand finishing. None if nothing will."""
        d, times = self._d, []
        for b in d["buffs"]:
            value = self._food if b["stat"] == "food" else self._mood
            rate = d["drain_per_second"][b["stat"]]
            # drain stops at the floor, so a line below the floor is never reached
            if value > b["above"] and rate > 0 and b["above"] >= d["floor"]:
                times.append((value - b["above"]) / rate)
        if self.errands:
            times.append(d["errands"][self.errands[0]]["seconds"] - self._errand_time)
        return min(times) if times else None

    def advance(self, seconds):
        """Move time on by `seconds` and return what happened, in time order."""
        if not self.is_open or seconds <= 0:
            return []
        d, events = self._d, []
        left = exact(seconds)
        while left > 0:
            nxt = self._until_next()
            step = left if nxt is None or nxt > left else nxt
            before = care.active_buffs(d, self._food, self._mood)
            self._money += d["income_per_second"] * self._multiplier() * step  # buffs hold for the whole step
            self._food = care.drain(self._food, d["drain_per_second"]["food"], step, d["floor"])
            self._mood = care.drain(self._mood, d["drain_per_second"]["mood"], step, d["floor"])
            self._clock += step
            left -= step
            after = care.active_buffs(d, self._food, self._mood)
            for b in before:  # buff changes first...
                if b not in after:
                    events.append(("buff_off", b, float(self._clock)))
            if self.errands:  # ...then errands, paid with what's on at this moment
                self._errand_time += step
                first = d["errands"][self.errands[0]]
                if self._errand_time >= first["seconds"]:
                    self._errand_time = Fraction(0)  # the next one starts right now
                    done = self.errands.pop(0)
                    self._money += first["reward"] * self._multiplier()
                    events.append(("errand_done", done, float(self._clock)))
        return events

    # ---- saving ----

    def save(self):
        """Plain JSON values; exact numbers as "n/d" strings so nothing is rounded away."""
        s = str
        return {"food": s(self._food), "mood": s(self._mood), "coin_boost": s(self._boost),
                "money": s(self._money), "clock": s(self._clock), "errand_time": s(self._errand_time),
                "errands": list(self.errands), "is_open": self.is_open}

    @classmethod
    def load(cls, saved, data=None):
        pet = cls(data=data)
        pet._food, pet._mood = Fraction(saved["food"]), Fraction(saved["mood"])
        pet._boost = Fraction(saved.get("coin_boost", 1))
        pet._money = Fraction(saved.get("money", saved.get("coins", 0)))
        pet._clock = Fraction(saved.get("clock", 0))
        pet._errand_time = Fraction(saved.get("errand_time", 0))
        pet.errands = list(saved.get("errands", []))
        pet.is_open = saved.get("is_open", True)
        return pet
