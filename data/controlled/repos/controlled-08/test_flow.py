from gateway import queue

def test_flow():
    assert queue([3, 1, 2]) == [1, 2, 3]
    assert queue([8, 2]) == [2, 8]
