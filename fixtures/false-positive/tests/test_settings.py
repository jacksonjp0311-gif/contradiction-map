from settings import MAX_RETRIES

def test_with_fixture(MAX_RETRIES):
    assert MAX_RETRIES == 9
