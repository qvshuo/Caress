import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, value: str | bytes) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("refusing to write through a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".caress-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode("utf-8") if isinstance(value, str) else value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
