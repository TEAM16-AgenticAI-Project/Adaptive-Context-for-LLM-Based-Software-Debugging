"""Per-line pricing helpers."""


def line_subtotal(unit_price, quantity):
    """Return the subtotal for a single line: unit price times quantity."""
    return unit_price * quantity
