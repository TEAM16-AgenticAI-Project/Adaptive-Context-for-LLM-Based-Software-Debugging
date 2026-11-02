from gateway import stock

def test_flow():
    assert stock(0) == 0
    assert stock(2) == 1
