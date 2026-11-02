import json
import sqlite3
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def runtime_directory(output: Path) -> Path:
    return Path("/data/runtime") / sha256(str(output.resolve()).encode()).hexdigest()[:16]


class RuntimeStore:
    """Runtime records in SQLite; NFO, images and maintained mappings stay files."""

    def __init__(self, root: Path):
        self.root = runtime_directory(root)
        if self.root.is_symlink():
            raise ValueError("runtime directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        database = self.root / "runtime.sqlite3"
        for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("runtime database must be a regular file")
        self.connection = sqlite3.connect(database, timeout=5)
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
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
            self.works_data = self._read("scan").get("catalog", [])
            self._index_works()
            self.results = self._read("result")
            self.issues = self._read("actor")
            self.task_data = {
                ident: {**json.loads(data), "items": []}
                for ident, data in self.connection.execute("SELECT id, data FROM tasks")
            }
            self.task_sequence = self._read("counter").get("task", 0)
            if any(task["sequence"] > self.task_sequence for task in self.task_data.values()):
                raise ValueError("task sequence counter is inconsistent")
            for ident, data in self.connection.execute(
                "SELECT task_id, data FROM task_items ORDER BY position"
            ):
                self.task_data[ident]["items"].append(json.loads(data))
            for task in list(self.task_data.values()):
                if task["status"] in ("pending", "running"):
                    task.update(status="finished", error="process interrupted", finished_at=now())
                    for item in task["items"]:
                        if item["status"] in ("pending", "running"):
                            item.update(status="failed", result={"error": "process interrupted"})
                    self.save_task(task)
        except Exception:
            self.connection.close()
            raise

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
        with self.connection:
            self._write("scan", "catalog", data)
        self.works_data = data
        self._index_works()

    def clear_scan(self):
        with self.connection:
            self.connection.execute("DELETE FROM records WHERE kind='scan'")
        self.works_data = []
        self._index_works()

    def save_issues(self, issues):
        with self.connection:
            self.connection.execute("DELETE FROM records WHERE kind='actor'")
            for name, issue in issues.items():
                self._write("actor", name, issue)
        self.issues = issues

    def close(self):
        self.connection.close()

    def delete_task(self, ident):
        with self.connection:
            self.connection.execute("DELETE FROM tasks WHERE id=?", (ident,))
        self.task_data.pop(ident, None)

    def save_task(self, task, item=None):
        if item is not None and task["id"] in self.task_data:
            old = self.connection.execute("SELECT json_extract(data, '$.status') FROM task_items WHERE task_id=? AND code=?", (task["id"], item["code"])).fetchone()
            if old is not None:
                with self.connection:
                    self.connection.execute("UPDATE task_items SET data=? WHERE task_id=? AND code=?", (json.dumps(item, ensure_ascii=False), task["id"], item["code"]))
                    if old[0] != item["status"]:
                        for key, statuses in {"progress": {"success", "partial", "failed", "cancelled", "skipped"}, "success": {"success"}, "partial": {"partial"}, "failed": {"failed"}, "skipped": {"skipped", "cancelled"}}.items():
                            task[key] += int(item["status"] in statuses) - int(old[0] in statuses)
                        self.connection.execute("UPDATE tasks SET data=json_set(data, '$.progress', ?, '$.success', ?, '$.partial', ?, '$.failed', ?, '$.skipped', ?, '$.current', json(?)) WHERE id=?", (task["progress"], task["success"], task["partial"], task["failed"], task["skipped"], json.dumps(task.get("current"), ensure_ascii=False), task["id"]))
                # The live header is a projection; detailed progress is durable in the item.
                self.task_data[task["id"]] = task
                return
        counts = Counter(entry["status"] for entry in task["items"])
        task.update(progress=sum(counts[s] for s in ("success", "partial", "failed", "cancelled", "skipped")), success=counts["success"], partial=counts["partial"], failed=counts["failed"], skipped=counts["skipped"] + counts["cancelled"])
        with self.connection:
            if task["id"] not in self.task_data:
                task["sequence"] = self.task_sequence + 1
                self._write("counter", "task", task["sequence"])
            header = {key: value for key, value in task.items() if key != "items"}
            self.connection.execute("INSERT INTO tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (task["id"], json.dumps(header, ensure_ascii=False)))
            self.connection.executemany("INSERT INTO task_items VALUES (?, ?, ?, ?) ON CONFLICT(task_id, code) DO UPDATE SET data=excluded.data", ((task["id"], entry["code"], position, json.dumps(entry, ensure_ascii=False)) for position, entry in enumerate(task["items"])))
        self.task_data[task["id"]] = task
        self.task_sequence = max(self.task_sequence, task["sequence"])

    def task(self, task_id, include_items=True):
        task = self.task_data[task_id]
        return dict(task) if include_items else {k: v for k, v in task.items() if k != "items"}

    def tasks(self, ids=None):
        rows = self.task_data.values() if ids is None else (self.task_data[ident] for ident in ids if ident in self.task_data)
        return [
            self.task(t["id"], False)
            for t in sorted(rows, key=lambda t: t["created_at"], reverse=True)
        ]

    def works(self, status=None, *, codes=None):
        rows = self.works_data if codes is None else (row for code in codes for row in self._by_code.get(code, []))
        return [dict(row) for row in rows if status is None or row["status"] == status]

    def _index_works(self):
        self._by_code, self._by_path = {}, {}
        for row in self.works_data:
            self._by_code.setdefault(row["code"], []).append(row)
            self._by_path[row["path"]] = row

    def work_row(self, code):
        values = self._by_code.get(code, [])
        if len(values) != 1 or values[0]["status"] in ("invalid_key", "conflict"):
            raise ValueError("work code is missing, invalid or conflicting")
        return values[0]

    def work(self, code):
        from .scanner import Work
        return Work.model_validate(self.work_row(code))

    def update_work(self, work):
        row = self.work_row(work.code)
        if row["path"] != str(work.path):
            raise ValueError("work path changed; scan required")
        value = work.model_dump(mode="json")
        row.clear()
        row.update(value)

    def work_at(self, path):
        return self._by_path.get(path)

    def save_scan(self, works):
        codes = {work.code for work in works}
        with self.connection:
            for (code,) in self.connection.execute("SELECT DISTINCT code FROM variants").fetchall():
                if code not in codes:
                    self.connection.execute("DELETE FROM variants WHERE code=?", (code,))
                    self.connection.execute("DELETE FROM selections WHERE code=?", (code,))
        data = [m.model_dump(mode="json") for m in works]
        self.save_catalog(data)

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
        with self.connection:
            self._write("result", key, result)
            if selection is not None:
                self._save_selection(key, selection)
            for issue in result.get("actor_issues", []):
                self._write("actor", issue["name"], issues[issue["name"]])
        self.results = {**self.results, key: result}
        self.issues = issues

    def variants(self, code):
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT data FROM variants WHERE code=? ORDER BY rowid", (code,))]

    def save_variant(self, code, variant):
        from .models import SourceVariant
        variant = SourceVariant.model_validate(variant).model_dump(mode="json")
        with self.connection:
            self.connection.execute(
                "INSERT INTO variants VALUES (?, ?, ?) ON CONFLICT(code, source) DO UPDATE SET data=excluded.data",
                (code, variant["source"], json.dumps(variant, ensure_ascii=False)))

    def selection(self, code):
        row = self.connection.execute("SELECT data FROM selections WHERE code=?", (code,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_selection(self, code, selection):
        with self.connection:
            self._save_selection(code, selection)

    def _save_selection(self, code, selection):
        self.connection.execute(
            "INSERT INTO selections VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET data=excluded.data",
            (code, json.dumps(selection, ensure_ascii=False)))

    def cooldowns(self):
        return self._read("cooldown")

    def save_cooldown(self, source, value):
        with self.connection:
            self._write("cooldown", str(source), value)

    def clear_cooldown(self, source):
        with self.connection:
            self.connection.execute(
                "DELETE FROM records WHERE kind='cooldown' AND (id=? OR id LIKE ?)",
                (str(source), str(source) + ":%"))
