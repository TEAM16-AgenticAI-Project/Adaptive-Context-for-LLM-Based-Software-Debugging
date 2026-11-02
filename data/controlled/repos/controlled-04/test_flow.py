from gateway import session

def test_flow():
    assert session(3600) == True
    assert session(3599) == False
    assert session(3601) == True
