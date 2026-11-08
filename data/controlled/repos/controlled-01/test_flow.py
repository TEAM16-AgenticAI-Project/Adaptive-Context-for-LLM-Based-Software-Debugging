from gateway import receipt

def test_flow():
    assert receipt(3) == 15
    assert receipt(2) == 10
