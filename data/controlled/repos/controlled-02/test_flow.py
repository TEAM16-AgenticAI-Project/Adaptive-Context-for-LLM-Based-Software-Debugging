from gateway import admission

def test_flow():
    assert admission(18) == True
    assert admission(17) == False
    assert admission(19) == True
