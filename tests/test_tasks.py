import asyncio

import pytest

from caress.models import WorkMetadata
from caress.service import Caress
from caress.settings import Settings
from caress.tasks import TaskRequest


@pytest.fixture
async def service(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "ABC-123.strm").touch()
    (media / "DEF-456-C.strm").touch()
    service = Caress(Settings(database=tmp_path / "data" / "caress.sqlite3", media_directories=[media]))
    await service.scan_async()
    try:
        yield service
    finally:
        await service.close()


class Processor:
    def __init__(self, service, request):
        self.service, self.request = service, request
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        pass


@pytest.mark.asyncio
async def test_queued_tasks_read_predecessor_output_and_keep_history(service, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    class Stub(Processor):
        async def process(self, work):
            calls.append((self.request.type, self.service.runtime.metadata(work.code)))
            if self.request.type == "refresh":
                entered.set()
                await release.wait()
                self.service.runtime.save_metadata(work.code, WorkMetadata(code=work.code, title="predecessor"))
            return {"code": work.code, "status": "found"}
    monkeypatch.setattr("caress.tasks.WorkProcessor", Stub)
    first = service.tasks.create(TaskRequest(type="refresh", codes=["ABC-123"]))
    await entered.wait()
    assert service.work_running("ABC-123")
    second = service.tasks.create(TaskRequest(type="fill_missing", codes=["ABC-123"]))
    assert len(calls) == 1
    release.set()
    await asyncio.gather(*list(service.tasks.running.values()))
    assert calls[1][1].title == "predecessor"
    assert service.runtime.task(first["id"])["success"] == 1
    assert service.runtime.task(second["id"])["sequence"] == first["sequence"] + 1
    service.runtime.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="edited"))
    assert service.runtime.task(first["id"])["items"][0]["result"]["status"] == "found"


@pytest.mark.asyncio
async def test_cancel_retains_committed_success_and_retry_selects_only_failed(service, monkeypatch):
    entered = asyncio.Event()
    class Stub(Processor):
        async def process(self, work):
            if work.code == "DEF-456":
                entered.set()
                await asyncio.Event().wait()
            self.service.runtime.save_metadata(work.code, WorkMetadata(code=work.code, title="saved"))
            return {"code": work.code, "status": "found"}
    monkeypatch.setattr("caress.tasks.WorkProcessor", Stub)
    record = service.tasks.create(TaskRequest(type="refresh"))
    await entered.wait()
    await asyncio.sleep(0)
    cancelled = await service.tasks.cancel(record["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["success"] == 1
    assert cancelled["skipped"] == 1
    assert service.runtime.metadata("ABC-123").title == "saved"
    retry = service.tasks.retry(record["id"])
    assert retry["params"]["codes"] == ["DEF-456"]
    assert retry["params"]["retry_only"]
    await service.tasks.cancel(retry["id"])


@pytest.mark.asyncio
async def test_configuration_changes_activate_only_at_batch_boundary(service, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []
    old_translator = service.translator
    class Stub(Processor):
        async def process(self, work):
            seen.append(service.settings.timeout_seconds)
            entered.set()
            await release.wait()
            seen.append(service.settings.timeout_seconds)
            return {"code": work.code, "status": "found"}
    monkeypatch.setattr("caress.tasks.WorkProcessor", Stub)
    service.tasks.create(TaskRequest(type="refresh", codes=["ABC-123"]))
    await entered.wait()
    updated = service.settings.model_copy(update={"timeout_seconds": 99})
    updated.save(connection=service.runtime.connection)
    service.runtime.connection.commit()
    release.set()
    await asyncio.gather(*list(service.tasks.running.values()))
    assert seen == [25, 25]
    assert service.settings.timeout_seconds == 99
    assert old_translator.client.is_closed


@pytest.mark.asyncio
async def test_actor_tasks_require_explicit_selection(service):
    for operation in ("update_portraits", "remap_actors"):
        with pytest.raises(ValueError, match="actors"):
            service.tasks.create(TaskRequest(type=operation))


@pytest.mark.asyncio
async def test_current_file_health_is_independent_of_historical_execution_and_emby(service):
    runtime = service.runtime
    runtime.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="saved"))
    runtime.save_selection("ABC-123", {"artwork": {"roles": {"poster": {
        "file": "ABC-123-poster.png", "choice": {"source": "custom"}, "protected": True, "status": "ready"}}}})
    runtime.record_result("ABC-123", {"status": "found", "incomplete_stages": []})
    media = runtime.media("ABC-123")[0]
    runtime.save_sync(media["id"], {"status": "failed", "error": "test Emby failure"})
    health = service.health("ABC-123")
    assert health.metadata
    assert len(health.artwork_problems) == 1
    assert health.incomplete_stages == []
    assert runtime.result("ABC-123")["status"] == "found"
    assert runtime.media("ABC-123")[0]["sync"]["status"] == "failed"


@pytest.mark.asyncio
async def test_batch_never_exceeds_ten_simultaneous_works(service, monkeypatch):
    for number in range(12):
        (service.settings.media_directories[0] / f"TESTABC-{number+100}.strm").touch()
    await service.scan_async()
    current = maximum = 0
    class Stub(Processor):
        async def process(self, work):
            nonlocal current, maximum
            current += 1
            maximum = max(maximum, current)
            await asyncio.sleep(.01)
            current -= 1
            return {"code": work.code, "status": "found"}
    monkeypatch.setattr("caress.tasks.WorkProcessor", Stub)
    task = service.tasks.create(TaskRequest(type="refresh"))
    await asyncio.gather(*list(service.tasks.running.values()))
    assert maximum == 10
    assert service.runtime.task(task["id"])["success"] == 14


@pytest.mark.asyncio
async def test_manual_edit_keeps_work_lock_until_sync_finishes(service, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    acquired = asyncio.Event()
    async def sync(code):
        assert service.work_lock(code).locked()
        entered.set()
        await release.wait()
        return []
    monkeypatch.setattr(service.emby, "sync", sync)
    form = WorkMetadata(code="ABC-123", title="manual edit").model_dump(mode="json", exclude={"code"})
    save = asyncio.create_task(service.operations.save_metadata("ABC-123", form))
    await entered.wait()
    async def competing_operation():
        async with service.work_lock("ABC-123"):
            acquired.set()
    competing = asyncio.create_task(competing_operation())
    await asyncio.sleep(0)
    assert not acquired.is_set()
    release.set()
    await asyncio.gather(save, competing)
    assert acquired.is_set()
    assert service.runtime.metadata("ABC-123").title == "manual edit"
