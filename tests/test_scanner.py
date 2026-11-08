from pathlib import Path

import pytest

from caress.scanner import parse_filename, scan


@pytest.mark.parametrize("filename,code,subtitles,uncensored,part", [
    ("ABC-123.mp4", "ABC-123", False, False, None),
    ("abc-123-c.STRM", "ABC-123", True, False, None),
    ("ABC-123-U-cd2.mkv", "ABC-123", False, True, 2),
    ("ABC-123-UC-CD12.ts", "ABC-123", True, True, 12),
    ("123456_123-C.strm", "123456_123", True, False, None),
    ("FC2PPV-123456-UC.strm", "FC2PPV-123456", True, True, None),
])
def test_exact_filename(filename, code, subtitles, uncensored, part):
    actual, media = parse_filename(Path("/unrelated/ZZZ-999") / filename)
    assert (actual, media.subtitles, media.uncensored, media.part) == (code, subtitles, uncensored, part)
    assert media.filename == filename


@pytest.mark.parametrize("filename", ["title.mp4", "ABC-123-HD.mp4", "ABC-123 CD1.mp4", "ABC-123-C-more.mkv", "ABC-123-cd1-cd2.mp4"])
def test_unsupported_cleanup(filename):
    with pytest.raises(ValueError):
        parse_filename(Path("/ABC-123") / filename)


def test_scan_groups_versions_keeps_invalid_and_never_reads_strm(tmp_path):
    parent = tmp_path / "ABC-123"
    parent.mkdir()
    names = ["ABC-123-C.strm", "ABC-123-U-cd1.mkv", "ABC-123-U-cd2.mp4", "not-a-code.strm"]
    for name in names:
        (parent / name).write_text("https://example.invalid/media")
    works = scan([tmp_path])
    work = next(work for work in works if work.code == "ABC-123")
    assert len(work.media) == 3
    assert len({media.id for media in work.media}) == 3
    invalid = next(work for work in works if work.status == "invalid_key")
    assert invalid.media[0].filename == "not-a-code.strm"
    assert sorted(path.name for path in parent.iterdir()) == sorted(names)
