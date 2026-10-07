"""Happy hour: when it is, and what it takes off."""

HAPPY_HOUR = range(15, 17)  # 15:00-16:59
HAPPY_HOUR_DISCOUNT = 0.20  # 20% off


def happy_hour_price(price, hour):
    """The price after any happy hour discount."""
    if hour in HAPPY_HOUR:
        return price * (1 - HAPPY_HOUR_DISCOUNT)
    return price
