from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

task_id = ContextVar("task_id", default=None)
work_code = ContextVar("work_code", default=None)


def report_progress(
    stage: str,
    status: str = "running",
    source=None,
    message: str | None = None,
) -> None:
    logging.getLogger('caress.progress').info(message or stage, extra={
        'event': 'processing.progress', 'stage': stage, 'source': source, 'result': status,
    })


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.funcName),
            "source": getattr(record, "source", None),
            "stage": getattr(record, "stage", None),
            "elapsed_seconds": getattr(record, "elapsed_seconds", None),
            "result": getattr(record, "result", None),
            "task_id": task_id.get(),
            "code": work_code.get(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False)


def configure() -> None:
    logger = logging.getLogger("caress")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
