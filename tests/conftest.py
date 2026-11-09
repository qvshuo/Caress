import pytest

from caress.runtime import Runtime


@pytest.fixture
def store(tmp_path):
    runtime = Runtime(tmp_path / "caress.sqlite3")
    try:
        yield runtime
    finally:
        runtime.close()
