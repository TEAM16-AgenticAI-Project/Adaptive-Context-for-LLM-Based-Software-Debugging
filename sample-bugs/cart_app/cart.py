"""Shopping cart: turns a list of line items into a final amount due."""
from pricing import line_subtotal
from tax import apply_tax


def cart_total(items, discount_rate, tax_rate):
    """Return the final amount due for a cart.

    items is a list of (unit_price, quantity). The customer pays the discounted
    subtotal plus tax charged on that discounted amount.
    """
    subtotal = sum(line_subtotal(price, qty) for price, qty in items)
    discounted = subtotal * (1 - discount_rate)
    return apply_tax(subtotal, discount_rate, tax_rate)
