from gateway import label

def test_flow():
    assert label('  ready  ') == 'ready'
    assert label('ok') == 'ok'
