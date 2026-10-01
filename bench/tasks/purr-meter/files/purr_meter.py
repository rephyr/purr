"""How happy is the cat? Turns pets into a purr level."""


def purr_level(pets):
    """0 pets: silent, 1-2: soft, 3-5: loud, 6 or more: motorboat."""
    if pets == 0:
        return "silent"
    if pets < 2:
        return "soft"
    if pets <= 5:
        return "loud"
    return "motorboat"


if __name__ == "__main__":
    for n in (0, 2, 4, 9):
        print(n, "pets ->", purr_level(n))
