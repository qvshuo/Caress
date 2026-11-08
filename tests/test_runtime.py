import sqlite3

import pytest

from caress.models import WorkMetadata
from caress.runtime import RuntimeStore
from caress.scanner import scan


def task(ident="t1"):
    return {"id": ident, "status": "pending", "created_at": "2026-01-01", "items": [
        {"code": "ABC-123", "status": "pending", "result": None}], "current": None}


def test_nested_rollback_keeps_database_and_projections_consistent(store):
    with pytest.raises(RuntimeError), store.transaction():
        store.save_catalog([{"code": "ABC-123", "status": "pending"}])
        store.save_task(task())
        store.record_result("ABC-123", {"status": "found"})
        store.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="title"))
        raise RuntimeError("commit failed")
    assert store.works() == []
    assert store.tasks() == []
    assert store.results == {}
    assert store.metadata("ABC-123") is None
    assert store.task_sequence == 0


def test_task_views_cannot_mutate_committed_data(store):
    record = task()
    store.save_task(record)
    record["items"][0]["status"] = "success"
    view = store.task("t1")
    assert view["items"][0]["status"] == "pending"
    view["items"][0]["status"] = "failed"
    assert store.task("t1")["items"][0]["status"] == "pending"


def test_task_sequence_never_reuses_deleted_or_nested_ids(store):
    with store.transaction():
        store.save_task(task("t1"))
        store.save_task(task("t2"))
    assert store.task("t2")["sequence"] == 2
    store.delete_task("t2")
    store.save_task(task("t3"))
    assert store.task("t3")["sequence"] == 3


def test_restart_restores_metadata_media_sync_and_execution(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "ABC-123-C.strm").touch()
    path = tmp_path / "data" / "caress.sqlite3"
    store = RuntimeStore(path)
    store.save_scan(scan([media]))
    store.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="saved"))
    store.save_task(task())
    ident = store.media("ABC-123")[0]["id"]
    store.save_sync(ident, {"status": "syncing", "item_id": "emby-id"})
    store.close()
    restored = RuntimeStore(path)
    try:
        assert restored.metadata("ABC-123").title == "saved"
        assert restored.media("ABC-123")[0]["sync"]["status"] == "pending"
        assert restored.task("t1")["items"][0]["status"] == "failed"
        assert restored.task("t1")["error"] == "process interrupted"
    finally:
        restored.close()


def test_schema_is_rejected_instead_of_migrated(tmp_path):
    path = tmp_path / "bad.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE records (old TEXT)")
    with pytest.raises(ValueError, match="全新数据库"):
        RuntimeStore(path)


def test_failed_multi_record_save_does_not_publish_any_cache(store, monkeypatch):
    original = store._write
    def fail(kind, key, value):
        if kind == "actor":
            raise sqlite3.OperationalError("disk full")
        original(kind, key, value)
    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.record_result("ABC-123", {"status": "found", "actor_issues": [{"name": "a"}]}, selection={"manual_fields": ["title"]})
    assert store.results == {}
    assert store.issues == {}
    assert store.selection("ABC-123") == {}


def test_actual_sqlite_commit_failure_never_publishes_nested_writes(store):
    with pytest.raises(sqlite3.IntegrityError), store.transaction():
        store.connection.execute("PRAGMA defer_foreign_keys=ON")
        store.save_catalog([{"code": "ABC-123", "status": "pending"}])
        store.save_task(task())
        store.connection.execute("INSERT INTO task_items VALUES ('missing-task', 'ABC-999', 0, '{}')")
    assert store.works() == []
    assert store.tasks() == []
    assert store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert store.task_sequence == 0


def test_scan_persists_identity_without_copying_final_metadata_or_queue_state(store):
    store.save_catalog([{"code": "ABC-123", "status": "queued", "title": "projection", "actors": ["actor"], "poster": "/thumb"}])
    saved = store._read("scan")["catalog"]
    assert saved == [{"code": "ABC-123", "status": "pending", "warnings": []}]
    assert store.find_work("ABC-123")["title"] == "projection"


def test_temporary_media_disappearance_preserves_selections_and_candidates(store, tmp_path):
    from caress.models import SourceId, SourceMetadata, SourceResult, Status
    media = tmp_path / "media"
    media.mkdir()
    file = media / "ABC-123.strm"
    file.touch()
    store.save_scan(scan([media]))
    store.save_selection("ABC-123", {"manual_fields": ["title"]})
    source = SourceResult(source=SourceId.DMM, requested_code="ABC-123", status=Status.FOUND, metadata=SourceMetadata(code="ABC-123", title="raw"))
    store.save_variant("ABC-123", source.model_dump(mode="json"))
    file.unlink()
    store.save_scan(scan([media]))
    file.touch()
    store.save_scan(scan([media]))
    assert store.selection("ABC-123")["manual_fields"] == ["title"]
    assert store.variants("ABC-123")[0]["metadata"]["title"] == "raw"


def test_independent_item_snapshots_preserve_other_items_and_atomic_counters(store):
    record = {**task(), "type": "refresh"}
    record["items"].append({"code": "DEF-456", "status": "pending", "result": None})
    store.save_task(record)
    first, second = store.task("t1"), store.task("t1")
    first["items"][0].update(status="success", stage={"time": "2026-01-01T00:00:01+00:00"})
    store.save_task(first, first["items"][0])
    second["items"][1].update(status="success", stage={"time": "2026-01-01T00:00:02+00:00"})
    store.save_task(second, second["items"][1])
    current = store.task("t1")
    assert current["success"] == current["progress"] == 2
    assert [item["status"] for item in current["items"]] == ["success", "success"]
    process = store.work_process("ABC-123")
    assert process["status"] == "success"
    process["status"] = "mutated"
    assert store.work_process("ABC-123")["status"] == "success"
    assert store.task_updates(["t1"], None) == {"ABC-123": "2026-01-01T00:00:01+00:00", "DEF-456": "2026-01-01T00:00:02+00:00"}


def test_item_commit_rollback_preserves_cache_counts_and_safe_retry(store):
    record = task()
    store.save_task(record)
    working = store.task("t1")
    working["items"][0]["status"] = "success"
    with pytest.raises(RuntimeError), store.transaction():
        store.save_task(working, working["items"][0])
        raise RuntimeError("outer commit failed")
    assert store.task("t1")["success"] == working["success"] == 0
    assert store.task("t1")["items"][0]["status"] == "pending"
    store.save_task(working, working["items"][0])
    assert store.task("t1")["success"] == working["success"] == 1


def test_task_pages_filter_sort_and_return_detached_items(store):
    record = {**task(), "type": "refresh"}
    record["items"] += [{"code": "DEF-456", "status": "failed", "result": None},
                        {"code": "GHI-789", "status": "success", "result": None}]
    store.save_task(record)
    page = store.task_page("t1", 1, 1, None, "")
    assert page["items"][0]["code"] == "DEF-456"
    assert (page["pages"], page["matched"]) == (3, 3)
    page["items"][0]["status"] = "mutated"
    assert store.work_process("DEF-456")["status"] == "failed"
    filtered = store.task_page("t1", 3, 100, {"success"}, "ghi")
    assert filtered["page"] == 1
    assert filtered["matched"] == 1
    assert filtered["items"][0]["code"] == "GHI-789"
