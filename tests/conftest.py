import pytest
import stamina


@pytest.fixture(autouse=True)
def _no_real_backoff():
    # keep the configured number of attempts, but never sleep for real in tests
    with stamina.set_testing(True, attempts=4, cap=True):
        yield


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """(facts.db, serve.db) built from every adapter fixture through the real pipeline. Read-only: copy before changing."""
    from tests.world import build_fixture_world

    return build_fixture_world(tmp_path_factory.mktemp("world"))
