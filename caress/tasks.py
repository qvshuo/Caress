"""Durable task scheduling and execution evidence; work processing lives separately."""
import asyncio
import logging
import time
import uuid
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .logging import progress, work_code
from .logging import task_id as log_task_id
from .processing import WorkProcessor
from .runtime import now

logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "fill_missing",
        "refresh",
        "update_portraits",
        "remap_actors",
    ]
    codes: list[str] = Field(default_factory=list)
    actor_names: list[str] = Field(default_factory=list)
    actor_ids: list[str] = Field(default_factory=list)
    retry_only: bool = False


class TaskQueue:
    def __init__(self, service):
        self.service = service
        self.running = {}
        self._run_lock = asyncio.Lock()
        self.running_codes = set()
        self.pending_counts = Counter()

    def active(self):
        return [task for task in self.service.runtime.tasks() if task["status"] in ("pending", "running")]

    def create(self, request: TaskRequest):
        if self.service.operation_lock.locked() and not self.active():
            raise ValueError("cannot create tasks while metadata is being edited")
        params = self.service.operations.task_parameters(request)
        keys = params["codes"]
        ident = uuid.uuid4().hex
        task = {
            "id": ident,
            "type": request.type,
            "status": "pending",
            "params": params,
            "total": len(keys),
            "progress": 0,
            "success": 0,
            "failed": 0,
            "created_at": now(),
            "finished_at": None,
            "error": None,
            "items": [
                {"code": key, "status": "pending", "attempts": 0, "result": None} for key in keys
            ],
        }
        queued = {"stage": "wait", "status": "pending", "source": None, "message": "任务已排队", "time": now()}
        for item in task["items"]:
            item.update(stage=dict(queued), events=[dict(queued)])
        self.service.runtime.save_task(task)
        for item in task["items"]:
            self.pending_counts[item["code"]] += 1
            self.service.refresh_work(item["code"])
        worker = asyncio.create_task(self.run(ident))
        self.running[ident] = worker
        def finished(worker):
            self.running.pop(ident, None)
            if not worker.cancelled() and (error := worker.exception()) is not None:
                logger.error("任务执行器异常", exc_info=(type(error), error, error.__traceback__),
                             extra={"event": "task.executor_failed", "stage": "finished", "result": "failed"})
        worker.add_done_callback(finished)
        return self.service.runtime.task(ident)

    def delete(self, ident):
        task = self.service.runtime.task_data[ident]
        if task["status"] in ("pending", "running"):
            raise ValueError("cancel the task before deleting it")
        self.service.runtime.delete_task(ident)
        return {"deleted": True}

    async def cancel(self, ident):
        task = self.service.runtime.task_data[ident]
        if task["status"] not in ("pending", "running"):
            raise ValueError("task is not active")
        worker = self.running.get(ident)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        task = self.service.runtime.task(ident)
        for item in task["items"]:
            if item["status"] in ("pending", "running"):
                if item["status"] == "pending":
                    self.pending_counts[item["code"]] -= 1
                item.update(status="cancelled", result=None)
                event = {
                    "stage": "finished",
                    "status": "cancelled",
                    "source": None,
                    "message": "任务已取消",
                    "time": now(),
                }
                item["events"] = [*item.get("events", []), event][-60:]
                item["stage"] = event
        task.update(status="cancelled", finished_at=now(), current=None)
        self.service.runtime.save_task(task)
        for code in task["params"]["codes"]:
            self.service.refresh_work(code)
        result = self.service.runtime.task(ident)
        if not self.service.closed and not self.active():
            async with self.service.operation_lock:
                await self.service.activate_settings()
        return result

    def retry(self, ident):
        task = self.service.runtime.task(ident)
        if task["status"] in ("running", "pending"):
            raise ValueError("cannot retry active task")
        keys = [i["code"] for i in task["items"] if i["status"] in ("failed", "cancelled", "partial")]
        if not keys:
            raise ValueError("task contains no failed items")
        return self.create(TaskRequest.model_validate({**{key: value for key, value in task["params"].items() if key in TaskRequest.model_fields}, "codes": keys, "retry_only": True}))

    async def run(self, ident):
        async with self._run_lock:
            await self._run(ident)
            async with self.service.operation_lock:
                await self.service.activate_settings()

    async def _run(self, ident):
        log_task_id.set(ident)
        task = self.service.runtime.task_data[ident]
        request = TaskRequest.model_validate(task["params"])
        task["status"] = "running"
        self.service.runtime.save_task(task)
        try:
            async with self.service.operation_lock:
                await self.service.activate_settings()
            async with WorkProcessor(self.service, request) as processor:
                async def process_item(item):
                    async with self.service.work_lock(item["code"]):
                        self.running_codes.add(item["code"])
                        try:
                            await process_locked_item(item)
                        finally:
                            self.running_codes.discard(item["code"])
                            self.service.refresh_work(item["code"])

                async def process_locked_item(item):
                    work_code.set(item["code"])
                    started = time.monotonic()
                    self.pending_counts[item["code"]] -= 1
                    item.update(status="running", attempts=item["attempts"] + 1)
                    self.service.runtime.save_task(task, item)

                    def update_progress(event):
                        events = item.get("events", [])
                        if event["stage"] == "wait" and event.get("source"):
                            events = [old for old in events if not (old["stage"] == "wait" and old.get("source") == event["source"])]
                        item["events"] = [*events, event][-60:]
                        item["stage"] = event
                        task["current"] = {"code": item["code"], **event}
                        self.service.runtime.save_task(task, item)

                    token = progress.set(update_progress)
                    try:
                        work = self.service.runtime.work(item["code"])
                        self.service.refresh_work(item["code"])
                        report = await processor.process(work)
                    except Exception as exc:
                        logger.exception("处理 %s 失败", item["code"])
                        report = {"code": item["code"], "status": "error", "error": str(exc)}
                    finally:
                        progress.reset(token)
                    task_report = report
                    outcome = "skipped" if task_report["status"] == "skipped" else "partial" if task_report.get("partial") else "success" if task_report["status"] in ("found", "unchanged") else "failed"
                    item.update(status=outcome, result=task_report)
                    update_progress({"stage": "finished", "status": outcome, "source": None,
                                     "message": None, "time": now()})
                    logger.info("作品处理完成", extra={"event": "work.processed", "stage": "finished",
                                "result": outcome, "elapsed_seconds": time.monotonic() - started})

                pending = iter(task["items"])
                async def worker():
                    for item in pending:
                        await process_item(item)
                async with asyncio.TaskGroup() as group:
                    for _ in range(min(10, len(task["items"]))):
                        group.create_task(worker())
            task.update(status="finished", current=None, finished_at=now())
        except Exception as exc:
            logger.exception("任务 %s 失败", ident)
            for item in task["items"]:
                if item["status"] in ("pending", "running"):
                    if item["status"] == "pending":
                        self.pending_counts[item["code"]] -= 1
                    event = {"stage": "finished", "status": "failed", "source": None,
                             "message": str(exc), "time": now()}
                    item.update(status="failed", result={"code": item["code"], "status": "error", "error": str(exc)}, stage=event)
                    item["events"] = [*item.get("events", []), event][-60:]
                    rows = self.service.runtime.works(codes=[item["code"]])
                    if len(rows) == 1 and rows[0]["status"] != "invalid_key":
                        self.service.refresh_work(item["code"])
            task.update(status="finished", current=None, error=str(exc), finished_at=now())
        self.service.runtime.save_task(task)

    async def close(self):
        for task in self.active():
            await self.cancel(task["id"])
