import pytest

from caress.runtime import RuntimeStore


@pytest.fixture
def store(tmp_path):
    runtime = RuntimeStore(tmp_path / "data" / "caress.sqlite3")
    try:
        yield runtime
    finally:
        runtime.close()
