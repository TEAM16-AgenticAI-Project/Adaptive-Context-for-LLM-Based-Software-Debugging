from discount import apply_discount


def checkout(price, discount):
    """Return the amount the customer pays."""
    apply_discount(price, discount)
    return price
