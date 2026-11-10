"""One cancellable execution queue; confirmed successes survive interruption."""

import asyncio
import uuid
from collections import Counter, deque
from tempfile import TemporaryDirectory

import httpx

from .emby import needs_enrichment, sync_error
from .library import identify
from .logging import task_id, work_code
from .processing import WorkProcessor
from .runtime import now


class BatchRunner:
    def __init__(self, service):
        self.service = service
        self.task = None
        self.batch = None
        self.queue = deque()

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def start(self, ids, action, source):
        if self.running and self.task.cancelling():
            raise ValueError("正在取消，请稍后再试")
        if not self.running:
            self.batch = {
                "id": uuid.uuid4().hex,
                "status": "running",
                "action": action,
                "source": source,
                "started_at": now(),
                "items": [],
                "current": [],
            }
            self.queue.clear()
        batch = self.batch
        active = {
            (row["id"], row["action"], row["source"])
            for row in batch["items"]
            if row["status"] in ("pending", "running")
        }
        rows = []
        for ident in dict.fromkeys(ids):
            if (ident, action, source) not in active:
                rows.append(
                    {
                        "id": ident,
                        "status": "pending",
                        "code": None,
                        "error": None,
                        "action": action,
                        "source": source,
                    }
                )
        if rows:
            batch["items"].extend(rows)
            self.queue.append({"action": action, "source": source, "items": rows})
        self.service.runtime.save_batch(batch)
        if not self.running:
            settings = self.service.settings.model_copy(deep=True)
            self.task = asyncio.create_task(self._drain(batch, settings))
        return batch

    async def _drain(self, batch, settings):
        allowed = set()
        try:
            while self.queue:
                job = self.queue.popleft()
                if any(row["status"] == "pending" for row in job["items"]):
                    await self._run(batch, job, settings, allowed)
            batch["status"] = "failed" if batch.get("error") else "finished"
        except asyncio.CancelledError:
            batch["status"] = "cancelled"
        except Exception as exc:
            batch.update(status="failed", error=sync_error(exc))
        finally:
            self.queue.clear()
            for row in batch["items"]:
                if row["status"] in ("pending", "running"):
                    row.update(
                        status="failed" if batch["status"] == "failed" else "cancelled",
                        error=batch.get("error") or "已取消；尚未开始写入",
                    )
            batch.update(current=[], finished_at=now())
            self.service.runtime.save_batch(batch)

    async def cancel(self):
        if self.running:
            task, ident = self.task, self.batch["id"]
            if not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            batch = self.service.runtime.batch()
            if batch and batch["id"] == ident and batch["status"] == "running":
                batch.update(status="cancelled", current=[], finished_at=now())
                for row in batch["items"]:
                    if row["status"] in ("pending", "running"):
                        row.update(status="cancelled", error="批次已取消")
                self.service.runtime.save_batch(batch)
            if self.task is task:
                self.queue.clear()

    async def _run(self, batch, job, settings, allowed):
        service, runtime = self.service, self.service.runtime
        task_token = task_id.set(batch["id"])
        lease = service.emby()
        client = None
        processor = None

        def save():
            batch["current"] = [
                i["id"] for i in batch["items"] if i["status"] == "running"
            ]
            runtime.save_batch(batch)

        try:
            client = await lease.__aenter__()
            # Resolve live parts/versions once and deduplicate Item IDs before any write.
            if not allowed:
                allowed.update({str(i["Id"]) async for i in client.items()})
            expanded = [row for row in job["items"] if row["status"] != "pending"]
            seen = {row["id"] for row in expanded}
            original = {id(row) for row in job["items"]}

            async def expand(ident):
                if ident in seen:
                    return
                seen.add(ident)
                if ident not in allowed and not await client.contains(ident):
                    expanded.append(
                        {
                            "id": ident,
                            "status": "skipped",
                            "code": None,
                            "error": "关联项目不属于已选择的媒体库或无法确认归属",
                        }
                    )
                    return
                try:
                    item = await client.item(ident)
                except (httpx.HTTPError, ValueError) as exc:
                    expanded.append(
                        {
                            "id": ident,
                            "status": "skipped",
                            "code": None,
                            "error": "无法确认关联项目：" + sync_error(exc),
                        }
                    )
                    return
                try:
                    code = identify(item)[0]
                except ValueError:
                    code = None
                expanded.append(
                    {"id": ident, "status": "pending", "code": code, "error": None}
                )
                for pending in batch["items"]:
                    if (
                        id(pending) not in original
                        and pending["id"] == ident
                        and pending["status"] == "pending"
                        and (pending["action"], pending["source"])
                        == (job["action"], job["source"])
                    ):
                        pending.update(status="skipped", note="已合并到同一作品的任务")
                try:
                    others = [str(p["Id"]) for p in await client.parts(item)]
                except (httpx.HTTPError, ValueError) as exc:
                    expanded[-1]["warnings"] = ["无法读取附加分集：" + sync_error(exc)]
                    others = []
                others += [
                    str(m["ItemId"])
                    for m in item.get("MediaSources", [])
                    if m.get("ItemId")
                ]
                for other in others:
                    if other != ident:
                        await expand(other)

            for row in job["items"]:
                if row["status"] == "pending":
                    await expand(row["id"])
            for row in expanded:
                row.update(action=job["action"], source=job["source"])
            # Keep submissions added while this job was resolving its associations.
            position = next(
                i for i, row in enumerate(batch["items"]) if id(row) in original
            )
            batch["items"] = [row for row in batch["items"] if id(row) not in original]
            batch["items"][position:position] = sorted(
                expanded, key=lambda row: row["code"] or ""
            )
            job["items"] = expanded
            remaining = Counter(row["code"] for row in expanded)
            save()
            with TemporaryDirectory(prefix="caress-batch-") as temporary:
                async with WorkProcessor(settings, runtime, temporary) as processor:
                    semaphore = asyncio.Semaphore(4)

                    async def work(row):
                        server, identity, code = client.server, None, None
                        writing = False
                        reserved_code = row["code"]
                        work_token = work_code.set(row["code"])
                        try:
                            async with semaphore, service.item_lock(server, row["id"]):
                                if row["status"] == "skipped":
                                    return
                                item = await client.item(row["id"])
                                code, identity, media = identify(item)
                                if code != row["code"]:
                                    raise ValueError(
                                        "Emby 项目番号已变化，请刷新后重试"
                                    )
                                old = runtime.record(server, row["id"], identity, code)
                                replace = job["action"] == "force"
                                if not replace and not needs_enrichment(
                                    item, await client.image_info(row["id"]), media
                                ):
                                    row.update(
                                        status="skipped", note="未发现需要补齐的内容"
                                    )
                                    return
                                row["status"] = "running"
                                runtime.save_record(
                                    server, row["id"], identity, code, "running"
                                )
                                save()
                                prepared = await processor.prepare(code, job["source"])
                                # Revalidate identity immediately before the write, after potentially slow scraping.
                                if not await client.contains(row["id"]):
                                    raise ValueError("项目已离开所选媒体库，未执行写入")
                                live_code, live_identity, live_media = identify(
                                    await client.item(row["id"])
                                )
                                if (live_code, live_identity) != (code, identity):
                                    raise ValueError(
                                        "刮削期间 Emby 项目身份变化，请刷新后重试"
                                    )
                                if self.task.cancelling():
                                    raise asyncio.CancelledError
                                writing = True
                                row["writing"] = True
                                runtime.save_record(
                                    server,
                                    row["id"],
                                    identity,
                                    code,
                                    "running",
                                    error="写入已开始，可能部分完成",
                                )
                                save()
                                write_task = asyncio.create_task(
                                    processor.write(
                                        client,
                                        row["id"],
                                        prepared,
                                        live_media,
                                        replace=replace,
                                    )
                                )
                                try:
                                    warnings, changed = await asyncio.shield(write_task)
                                except asyncio.CancelledError:
                                    warnings, changed = await write_task
                                if not changed:
                                    runtime.restore_record(server, row["id"], old)
                                    row.update(
                                        status="skipped",
                                        note="本次来源数据未产生变更",
                                        writing=False,
                                        warnings=[*row.get("warnings", []), *warnings],
                                    )
                                    return
                                runtime.save_record(
                                    server,
                                    row["id"],
                                    identity,
                                    code,
                                    "success",
                                    source=prepared.source,
                                )
                                row.update(
                                    status="success",
                                    warnings=[*row.get("warnings", []), *warnings],
                                )
                        except asyncio.CancelledError:
                            row.update(
                                status="cancelled",
                                error="已取消；写入可能已部分完成"
                                if writing
                                else "已取消；尚未开始写入",
                            )
                            if identity:
                                runtime.save_record(
                                    server,
                                    row["id"],
                                    identity,
                                    code,
                                    "cancelled",
                                    error=row["error"],
                                )
                            raise
                        except Exception as exc:
                            row.update(
                                status="failed",
                                error=("写入可能已部分完成：" if writing else "")
                                + sync_error(exc),
                            )
                            if identity:
                                runtime.save_record(
                                    server,
                                    row["id"],
                                    identity,
                                    code,
                                    "failed",
                                    error=row["error"],
                                )
                        finally:
                            work_code.reset(work_token)
                            remaining[reserved_code] -= 1
                            try:
                                if remaining[reserved_code] == 0:
                                    await processor.release(
                                        reserved_code, job["source"]
                                    )
                            finally:
                                save()

                    try:
                        async with asyncio.TaskGroup() as group:
                            for row in job["items"]:
                                group.create_task(work(row))
                    finally:
                        await processor.finish()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            batch["error"] = sync_error(exc)
            for row in job["items"]:
                if row["status"] in ("pending", "running"):
                    row.update(status="failed", error=sync_error(exc))
        finally:
            for row in job["items"]:
                if row["status"] in ("pending", "running"):
                    row.update(status="cancelled", error="已取消；尚未开始写入")
            save()
            try:
                if client is not None:
                    await lease.__aexit__(None, None, None)
            finally:
                task_id.reset(task_token)
