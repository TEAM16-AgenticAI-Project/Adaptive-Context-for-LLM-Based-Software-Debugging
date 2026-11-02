from gateway import selection

def test_flow():
    assert selection(['a', 'b']) == 'a'
    assert selection([4, 8]) == 4
