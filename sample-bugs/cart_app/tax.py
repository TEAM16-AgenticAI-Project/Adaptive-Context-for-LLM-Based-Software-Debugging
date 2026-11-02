"""Tax calculation."""


def apply_tax(subtotal, discount_rate, tax_rate):
    """Add tax to the DISCOUNTED amount.

    The customer is taxed on what they actually pay, so tax must be applied
    after the discount: taxed = subtotal * (1 - discount_rate) * (1 + tax_rate).
    """
    # BUG: taxes the full subtotal, ignoring the discount.
    return subtotal * (1 + tax_rate)
