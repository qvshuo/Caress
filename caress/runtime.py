import json
import sqlite3
from collections import Counter
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


class RuntimeStore:
    """SQLite owns structured data; image bytes stay on disk."""

    def __init__(self, database: Path):
        self.root = database.parent
        if self.root.is_symlink():
            raise ValueError("runtime directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("runtime database must be a regular file")
        self._sequence = 0
        self._commits = []
        self.connection = sqlite3.connect(database, timeout=5)
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if tables.intersection({"records", "tasks", "task_items", "media", "variants", "selections"}) and version != 2:
                raise ValueError("数据库结构版本不支持，请使用全新数据库；不提供旧版迁移")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind, id))"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS task_items (task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, "
                "code TEXT NOT NULL, position INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id, code))"
            )
            self.connection.execute("CREATE TABLE IF NOT EXISTS variants (code TEXT NOT NULL, source TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(code, source))")
            self.connection.execute("CREATE TABLE IF NOT EXISTS selections (code TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, code TEXT NOT NULL, path TEXT UNIQUE NOT NULL, data TEXT NOT NULL, sync TEXT NOT NULL DEFAULT '{}')")
            self.connection.execute("CREATE INDEX IF NOT EXISTS media_code_path ON media(code, path)")
            expected = {
                "records": ["kind", "id", "data"], "tasks": ["id", "data"],
                "task_items": ["task_id", "code", "position", "data"],
                "variants": ["code", "source", "data"], "selections": ["code", "data"],
                "media": ["id", "code", "path", "data", "sync"],
            }
            for table, columns in expected.items():
                actual = [row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")]
                if actual != columns:
                    raise ValueError(f"数据库结构异常：{table}，请使用全新数据库")
                for column in ("data", "sync") if table == "media" else ("data",):
                    if self.connection.execute(f"SELECT 1 FROM {table} WHERE NOT json_valid({column}) LIMIT 1").fetchone():
                        raise ValueError(f"数据库记录损坏：{table}.{column}")
            self.connection.execute("PRAGMA user_version=2")
            self.connection.execute("UPDATE media SET sync=json_set(sync, '$.status', 'pending', '$.error', '同步被中断') WHERE json_extract(sync, '$.status')='syncing'")
            self.connection.commit()
            from .models import Work
            self.works_data = [Work.model_validate(row).model_dump(mode="json", exclude={"media"})
                               for row in self._read("scan").get("catalog", [])]
            self._index_works()
            self._task_data = {
                ident: {**json.loads(data), "items": [], "current": None}
                for ident, data in self.connection.execute("SELECT id, data FROM tasks")
            }
            self.task_sequence = self._read("counter").get("task", 0)
            if any(task["sequence"] > self.task_sequence for task in self._task_data.values()):
                raise ValueError("task sequence counter is inconsistent")
            for ident, data in self.connection.execute(
                "SELECT task_id, data FROM task_items ORDER BY position"
            ):
                self._task_data[ident]["items"].append(json.loads(data))
            self._task_positions = {ident: {item["code"]: position for position, item in enumerate(task["items"])}
                                    for ident, task in self._task_data.items()}
            for task in list(self._task_data.values()):
                if task["status"] in ("pending", "running"):
                    task.update(status="finished", error="process interrupted", finished_at=now())
                    for item in task["items"]:
                        if item["status"] in ("pending", "running"):
                            item.update(status="failed", result={"error": "process interrupted"})
                    self.save_task(task)
        except Exception:
            self.connection.close()
            raise

    @property
    def results(self):
        return self._read("result")

    @property
    def issues(self):
        return self._read("actor")

    def _publish(self, callback):
        if self._commits:
            self._commits[-1].append(callback)
        else:
            callback()

    @contextmanager
    def transaction(self):
        self._sequence += 1
        name = f"write_{self._sequence}"
        self.connection.execute(f"SAVEPOINT {name}")
        callbacks = []
        self._commits.append(callbacks)
        try:
            yield
            self.connection.execute(f"RELEASE SAVEPOINT {name}")
        except BaseException:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            if len(self._commits) > 1:
                self._commits[-2].extend(callbacks)
            else:
                for callback in callbacks:
                    callback()
        finally:
            self._commits.pop()

    def media(self, code):
        return [json.loads(row[0]) | {"sync": json.loads(row[1])} for row in self.connection.execute(
            "SELECT data, sync FROM media WHERE code=? ORDER BY path", (code,))]

    def save_sync(self, ident, state):
        with self.transaction():
            self.connection.execute("UPDATE media SET sync=? WHERE id=?", (json.dumps(state, ensure_ascii=False), ident))

    def item_sync(self, server, ident):
        row = self.connection.execute("SELECT data FROM records WHERE kind='item_sync' AND id=?", (f"{server}:{ident}",)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_item_sync(self, server, ident, state):
        with self.transaction():
            self._write("item_sync", f"{server}:{ident}", state)

    def portrait_sync(self, server, person):
        row = self.connection.execute("SELECT data FROM records WHERE kind='portrait_sync' AND id=?", (f"{server}:{person}",)).fetchone()
        return json.loads(row[0]) if row else None

    def save_portrait_sync(self, server, person, digest):
        with self.transaction():
            self._write("portrait_sync", f"{server}:{person}", digest)

    def metadata(self, code):
        from .models import WorkMetadata
        row = self.connection.execute("SELECT data FROM records WHERE kind='metadata' AND id=?", (code,)).fetchone()
        return WorkMetadata.model_validate_json(row[0]) if row else None

    def metadata_snapshot(self):
        from .models import WorkMetadata
        return {code: WorkMetadata.model_validate_json(data) for code, data in self.connection.execute(
            "SELECT id, data FROM records WHERE kind='metadata'")}

    def save_metadata(self, code, metadata):
        with self.transaction():
            from .models import EDITABLE_FIELDS
            self._write("metadata", code, metadata.model_dump(mode="json", include={"code", *EDITABLE_FIELDS}))

    def _read(self, kind):
        return {
            key: json.loads(data)
            for key, data in self.connection.execute(
                "SELECT id, data FROM records WHERE kind = ?", (kind,)
            )
        }

    def _write(self, kind, key, value):
        self.connection.execute(
            "INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(kind, id) DO UPDATE SET data=excluded.data",
            (kind, key, json.dumps(value, ensure_ascii=False)),
        )

    def has_scan(self):
        return (
            self.connection.execute(
                "SELECT 1 FROM records WHERE kind='scan' AND id='catalog'"
            ).fetchone()
            is not None
        )

    def save_catalog(self, data):
        with self.transaction():
            # Only scanner identity is durable here; titles, artwork and queue
            # status are rebuilt projections of their respective owners.
            self._write("scan", "catalog", [{"code": row["code"],
                "status": "invalid_key" if row["status"] == "invalid_key" else "pending",
                "warnings": row.get("warnings", [])} for row in data])
        def publish():
            self.works_data = deepcopy(data)
            self._index_works()
        self._publish(publish)

    def clear_scan(self):
        with self.transaction():
            self.connection.execute("DELETE FROM records WHERE kind='scan'")
        def publish():
            self.works_data = []
            self._index_works()
        self._publish(publish)

    def save_issues(self, issues):
        with self.transaction():
            self.connection.execute("DELETE FROM records WHERE kind='actor'")
            for name, issue in issues.items():
                self._write("actor", name, issue)

    def close(self):
        self.connection.close()

    def delete_task(self, ident):
        with self.transaction():
            self.connection.execute("DELETE FROM tasks WHERE id=?", (ident,))
        def publish():
            self._task_data.pop(ident, None)
            self._task_positions.pop(ident, None)
        self._publish(publish)

    def save_task(self, task, item=None):
        if item is not None and task["id"] in self._task_data:
            old = self.connection.execute("SELECT json_extract(data, '$.status') FROM task_items WHERE task_id=? AND code=?", (task["id"], item["code"])).fetchone()
            if old is not None:
                with self.transaction():
                    self.connection.execute("UPDATE task_items SET data=? WHERE task_id=? AND code=?", (json.dumps(item, ensure_ascii=False), task["id"], item["code"]))
                    groups = {"progress": {"success", "partial", "failed", "cancelled", "skipped"},
                              "success": {"success"}, "partial": {"partial"}, "failed": {"failed"},
                              "skipped": {"skipped", "cancelled"}}
                    deltas = {key: int(item["status"] in statuses) - int(old[0] in statuses)
                              for key, statuses in groups.items()}
                    counts = {}
                    if any(deltas.values()):
                        fields = ", ".join(f"'$.{key}', json_extract(data, '$.{key}') + ?" for key in groups)
                        returning = ", ".join(f"json_extract(data, '$.{key}')" for key in groups)
                        row = self.connection.execute(f"UPDATE tasks SET data=json_set(data, {fields}) WHERE id=? RETURNING {returning}",
                            (*deltas.values(), task["id"])).fetchone()
                        counts = dict(zip(groups, row, strict=True))
                # The current pointer is a projection of the committed item;
                # immutable request parameters never need copying on each event.
                header = {"id": task["id"], **counts, "current": deepcopy(task.get("current"))}
                saved_item = deepcopy(item)
                def publish_item():
                    task.update(counts)
                    cached = self._task_data[header["id"]]
                    cached.update(header)
                    cached["items"][self._task_positions[header["id"]][saved_item["code"]]] = saved_item
                self._publish(publish_item)
                return
        counts = Counter(entry["status"] for entry in task["items"])
        task.update(progress=sum(counts[s] for s in ("success", "partial", "failed", "cancelled", "skipped")), success=counts["success"], partial=counts["partial"], failed=counts["failed"], skipped=counts["skipped"] + counts["cancelled"])
        with self.transaction():
            if not self.connection.execute("SELECT 1 FROM tasks WHERE id=?", (task["id"],)).fetchone():
                counter = self.connection.execute("SELECT data FROM records WHERE kind='counter' AND id='task'").fetchone()
                task["sequence"] = (json.loads(counter[0]) if counter else 0) + 1
                self._write("counter", "task", task["sequence"])
            header = {key: value for key, value in task.items() if key not in {"items", "current"}}
            self.connection.execute("INSERT INTO tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (task["id"], json.dumps(header, ensure_ascii=False)))
            self.connection.executemany("INSERT INTO task_items VALUES (?, ?, ?, ?) ON CONFLICT(task_id, code) DO UPDATE SET data=excluded.data", ((task["id"], entry["code"], position, json.dumps(entry, ensure_ascii=False)) for position, entry in enumerate(task["items"])))
        saved = deepcopy(task)
        def publish():
            self._task_data[saved["id"]] = saved
            self._task_positions[saved["id"]] = {item["code"]: position for position, item in enumerate(saved["items"])}
            self.task_sequence = max(self.task_sequence, saved["sequence"])
        self._publish(publish)

    def task(self, task_id, include_items=True):
        task = self._task_data[task_id]
        return deepcopy(task if include_items else {k: v for k, v in task.items() if k != "items"})

    def tasks(self, ids=None):
        rows = self._task_data.values() if ids is None else (self._task_data[ident] for ident in ids if ident in self._task_data)
        return [
            self.task(t["id"], False)
            for t in sorted(rows, key=lambda t: t["created_at"], reverse=True)
        ]

    def task_page(self, ident, page, page_size, statuses, query):
        items = self._task_data[ident]["items"]
        if statuses is not None:
            items = [item for item in items if item["status"] in statuses]
        if query:
            paths = {}
            for code, path in self.connection.execute("SELECT code, path FROM media"):
                paths.setdefault(code, []).append(path)
            needle = query.casefold()
            def matches(item):
                row = self.find_work(item["code"]) or {}
                return needle in " ".join([item["code"], row.get("title") or "",
                    *paths.get(item["code"], []), *row.get("actors", [])]).casefold()
            items = [item for item in items if matches(item)]
        items = sorted(items, key=lambda item: (item["status"] != "failed", item["code"]))
        pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(page, pages)
        return {**self.task(ident, False), "items": deepcopy(items[(page - 1) * page_size:page * page_size]),
                "page": page, "pages": pages, "matched": len(items)}

    def task_updates(self, ids, after):
        updates = {}
        for ident in ids:
            task = self._task_data.get(ident)
            if task is None:
                continue
            for item in task["items"]:
                event = item.get("stage")
                if item["status"] == "running" or (event and (after is None or datetime.fromisoformat(event["time"]) >= after)):
                    updates[item["code"]] = max(updates.get(item["code"]) or "", event["time"]) if event else None
        return updates

    def work_process(self, code, task_id=None):
        tasks = [self._task_data[task_id]] if task_id in self._task_data else []
        if task_id is None:
            tasks = sorted(self._task_data.values(), key=lambda task: task["created_at"], reverse=True)
        for task in tasks:
            position = self._task_positions[task["id"]].get(code)
            if position is not None:
                return {"task_id": task["id"], "type": task["type"], "sequence": task["sequence"],
                        "created_at": task["created_at"], "finished_at": task.get("finished_at"),
                        **deepcopy(task["items"][position])}
        return None

    def works(self, status=None, *, codes=None):
        rows = self.works_data if codes is None else (self._by_code[code] for code in codes if code in self._by_code)
        return [{**row, "media": self.media(row["code"])} for row in rows if status is None or row["status"] == status]

    def _index_works(self):
        self._by_code = {row["code"]: row for row in self.works_data}

    def find_work(self, code):
        return self._by_code.get(code)

    def work_row(self, code):
        value = self.find_work(code)
        if value is None or value["status"] == "invalid_key":
            raise ValueError("work code is missing or invalid")
        return value

    def work(self, code):
        from .models import Work
        return Work.model_validate({**self.work_row(code), "media": self.media(code)})

    def update_work(self, work):
        row = self.work_row(work.code)
        value = work.model_dump(mode="json", exclude={"media"})
        row.clear()
        row.update(value)

    def save_scan(self, works):
        with self.transaction():
            found = set()
            for work in works:
                for media in work.media:
                    found.add(media.id)
                    self.connection.execute("INSERT INTO media(id, code, path, data) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET code=excluded.code, path=excluded.path, data=excluded.data",
                        (media.id, work.code, str(media.path), media.model_dump_json()))
            for ident, in self.connection.execute("SELECT id FROM media").fetchall():
                if ident not in found:
                    self.connection.execute("DELETE FROM media WHERE id=?", (ident,))
            self.save_catalog([work.model_dump(mode="json", exclude={"media"}) for work in works])

    def result(self, code):
        row = self.connection.execute("SELECT data FROM records WHERE kind='result' AND id=?", (code,)).fetchone()
        return json.loads(row[0]) if row else {}

    def record_result(self, key, result, *, selection=None):
        result = deepcopy(result)
        issues = dict(self.issues)
        for issue in result.get("actor_issues", []):
            old = issues.get(issue["name"], {})
            issues[issue["name"]] = {
                **issue,
                "count": old.get("count", 0) + 1,
                "last_seen_at": now(),
            }
        with self.transaction():
            self._write("result", key, result)
            if selection is not None:
                self._save_selection(key, selection)
            for issue in result.get("actor_issues", []):
                self._write("actor", issue["name"], issues[issue["name"]])

    def variants(self, code):
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT data FROM variants WHERE code=? ORDER BY rowid", (code,))]

    def save_variant(self, code, variant):
        from .models import SourceVariant
        variant = SourceVariant.model_validate(variant).model_dump(mode="json")
        with self.transaction():
            self.connection.execute(
                "INSERT INTO variants VALUES (?, ?, ?) ON CONFLICT(code, source) DO UPDATE SET data=excluded.data",
                (code, variant["source"], json.dumps(variant, ensure_ascii=False)))

    def selection(self, code):
        row = self.connection.execute("SELECT data FROM selections WHERE code=?", (code,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_selection(self, code, selection):
        with self.transaction():
            self._save_selection(code, selection)

    def _save_selection(self, code, selection):
        from .models import WorkSelection
        selection = WorkSelection.model_validate(selection).model_dump(mode="json", exclude_unset=True, exclude_none=True)
        self.connection.execute(
            "INSERT INTO selections VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET data=excluded.data",
            (code, json.dumps(selection, ensure_ascii=False)))

    def cooldowns(self):
        return self._read("cooldown")

    def save_cooldown(self, source, value):
        with self.transaction():
            self._write("cooldown", str(source), value)

    def clear_cooldown(self, source):
        with self.transaction():
            self.connection.execute(
                "DELETE FROM records WHERE kind='cooldown' AND (id=? OR id LIKE ?)",
                (str(source), str(source) + ":%"))


def read_rules(database: Path, kind: str, seed: Path):
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS rules (kind TEXT PRIMARY KEY, data TEXT NOT NULL)")
        row = connection.execute("SELECT data FROM rules WHERE kind=?", (kind,)).fetchone()
        if row is None:
            connection.execute("INSERT OR IGNORE INTO rules VALUES (?, ?)", (kind, seed.read_text()))
            row = connection.execute("SELECT data FROM rules WHERE kind=?", (kind,)).fetchone()
        return json.loads(row[0])


def merge_rules(database: Path, kind: str, update):
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        value = json.loads(connection.execute("SELECT data FROM rules WHERE kind=?", (kind,)).fetchone()[0])
        value = update(value)
        connection.execute("UPDATE rules SET data=? WHERE kind=?", (json.dumps(value, ensure_ascii=False), kind))
        return value
