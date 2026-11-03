from settings import MAX_RETRIES

def test_retry_contract():
    assert MAX_RETRIES == 3
