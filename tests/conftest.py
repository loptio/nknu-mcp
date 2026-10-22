import pytest
import stamina


@pytest.fixture(autouse=True)
def _no_real_backoff():
    # keep the configured number of attempts, but never sleep for real in tests
    with stamina.set_testing(True, attempts=4, cap=True):
        yield
