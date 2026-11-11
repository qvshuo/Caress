from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from urllib.parse import urlencode

from ..actors import ActorAliases, actor_record, actor_type, comparison_name
from ..http import FetchError, json_error
from ..models import SourceId, Status


class JavDBApi:
    id = SourceId.JAVDB

    def __init__(self, client, settings) -> None:
        self.client = client
        self.settings = settings
        self.base = settings.javdb_api.base_url.rstrip("/")
        self.device_uuid = str(uuid.uuid4())

    async def api(self, path: str, **query: str | int) -> dict:
        settings = self.settings.javdb_api
        stamp = str(int(time.time()))
        signature = hashlib.md5(
            (stamp + settings.signature_prefix).encode(), usedforsecurity=False
        ).hexdigest()
        params = {
            "app_channel": "official",
            "app_version": settings.app_version,
            "app_version_number": settings.app_version_number,
            "platform": "android",
            "system_version": "13",
            "device_model": "Pixel 6",
            "device_name": "Pixel",
            "device_uuid": self.device_uuid,
            **query,
        }
        page = await self.client.get(
            self.id,
            self.base + path + "?" + urlencode(params),
            headers={
                "jdsignature": f"{stamp}.{settings.signature_version}.{signature}",
                "User-Agent": "Dart/3.4 (dart:io)",
                "Accept": "application/json",
                "Accept-Language": "zh-TW",
            },
        )
        payload = page.json()
        if not isinstance(payload, dict):
            raise FetchError(Status.PARSE_ERROR, "JavDB 返回的数据格式无效")
        if payload.get("success") not in (1, True):
            message = "JavDB 请求失败：" + json_error(payload)
            raise FetchError(Status.BLOCKED, message)
        if not isinstance(payload.get("data"), dict):
            raise FetchError(Status.PARSE_ERROR, "JavDB 接口未返回数据")
        return payload["data"]

    async def resolve_actors(
        self, actors, names: ActorAliases, category, ids, search
    ) -> list[str]:
        warnings = []
        if self.settings.database:
            names.reload(self.settings.database)
        kind = actor_type(category)
        for actor in actors:
            if actor.name in ids and ids[actor.name] is None:
                continue
            ident = ids.get(actor.name)
            if not names.needs_lookup(actor.name, kind, ident):
                continue
            try:
                candidates = [ident] if ident else await search(actor.name)
                if len(candidates) > 20:
                    warnings.append(f"{actor.name}: 找到多个演员候选，已保留原名")
                    continue
                records = {}
                for candidate in candidates:
                    if not re.fullmatch(r"[A-Za-z0-9]{1,40}", candidate):
                        continue
                    data = await self.api(f"/api/v1/actors/{candidate}")
                    row = data.get("actor")
                    if not isinstance(row, dict) or str(row.get("id")) != candidate:
                        raise ValueError("演员详情的身份不匹配")
                    record = actor_record(row)
                    if not record:
                        continue
                    if not ident and (
                        (kind is not None and record["type"] != kind)
                        or comparison_name(actor.name)
                        not in {
                            comparison_name(name)
                            for name in ActorAliases.record_names(record)
                        }
                    ):
                        continue
                    records[candidate] = record
                if len(records) == 1:
                    names.remember(records, self.settings.database)
                elif len(records) > 1:
                    warnings.append(f"{actor.name}: 无法唯一确认演员，已保留原名")
            except (FetchError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
                warnings.append(f"{actor.name}: 演员信息获取失败：{exc}")
        return warnings
