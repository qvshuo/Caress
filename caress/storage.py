import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_transaction(folder: Path):
    """Restore a work's files on failure, including a full disk during replacement.

    The caller holds the work lock. Hard links keep the original bytes on the
    same filesystem, so rollback needs renames rather than another allocation.
    This protects handled failures; it is not a crash-recovery journal.
    """
    if folder.is_symlink():
        raise ValueError("refusing to write through a symlink")
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".caress-save-", dir=folder) as temporary:
        backup = Path(temporary)
        originals = {path.name for path in folder.iterdir() if path.is_file()}
        for name in originals:
            path = folder / name
            if path.is_symlink():
                raise ValueError("refusing to write through a symlink")
            os.link(path, backup / name)
        try:
            yield
        except BaseException:
            for path in folder.iterdir():
                if path.name not in originals and (path.is_file() or path.is_symlink()):
                    path.unlink()
            for name in originals:
                os.replace(backup / name, folder / name)
            raise


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


def target_folder(output: Path, code: str) -> Path:
    from .scanner import validate_code
    validate_code(code)
    root = output.resolve()
    target = root / code
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("image target escapes output root")
    return target


def image_stems(work) -> list[str]:
    return [work.code]
