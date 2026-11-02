from relay import route

def receipt(value):
    """Checkout receipt charges the wrong amount for several units."""
    return route(value)
