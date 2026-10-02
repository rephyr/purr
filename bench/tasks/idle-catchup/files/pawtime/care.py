"""Food, mood and the buffs they give."""


def drain(value, per_second, seconds, floor):
    """A stat after `seconds` of draining, never below the floor."""
    return max(floor, value - per_second * seconds)


def buff_on(buff, food, mood):
    value = food if buff["stat"] == "food" else mood
    return value >= buff["above"]


def active_buffs(data, food, mood):
    return [b["id"] for b in data["buffs"] if buff_on(b, food, mood)]


def multiplier(data, food, mood, coin_boost=1.0):
    """Every buff that's on, times the pet's own boost."""
    m = coin_boost
    for b in data["buffs"]:
        if buff_on(b, food, mood):
            m *= b["x"]
    return m
