from gateway import levy

def test_flow():
    assert levy(100) == 20
    assert levy(50) == 10
