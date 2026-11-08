from cart import cart_total


def test_cart_total_applies_discount_before_tax():
    # one item: $100 x 1, 20% off, 10% tax.
    # correct: 100 * 0.8 * 1.1 = 88.0
    items = [(100.0, 1)]
    assert cart_total(items, discount_rate=0.20, tax_rate=0.10) == 88.0
