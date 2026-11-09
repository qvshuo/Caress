"""Small durable processing ledger; media and metadata belong to Emby."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


class Runtime:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        self.connection.row_factory = sqlite3.Row
        try:
            tables = {
                r[0]
                for r in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables - {"options", "rules", "scrapes", "batch", "cooldowns"}:
                raise ValueError(
                    "旧数据库须先备份并通过 python -m caress.import_settings 导入到新数据库"
                )
            with self.connection:
                self.connection.execute("PRAGMA journal_mode=WAL")
                self.connection.execute("""CREATE TABLE IF NOT EXISTS scrapes (
                    server TEXT NOT NULL, item TEXT NOT NULL, identity TEXT NOT NULL, code TEXT NOT NULL,
                    succeeded_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','success','failed','cancelled')),
                    error TEXT, source TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(server,item))""")
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS batch (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)"
                )
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS cooldowns (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
                self.connection.execute(
                    "UPDATE scrapes SET status='failed',error=CASE WHEN error IS NOT NULL THEN '服务重启，写入可能已部分完成' ELSE '服务重启，尚未开始写入' END WHERE status='running'"
                )
                batch = self.batch()
                if batch and batch["status"] == "running":
                    for item in batch["items"]:
                        if item["status"] in ("pending", "running"):
                            item.update(
                                status="cancelled",
                                error="服务重启，写入可能已部分完成"
                                if item.get("writing")
                                else "服务重启，尚未开始写入",
                            )
                    batch.update(status="interrupted", current=[])
                    self.connection.execute(
                        "UPDATE batch SET data=? WHERE id=1",
                        (json.dumps(batch, ensure_ascii=False),),
                    )
        except BaseException:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def record(self, server, item, identity, code):
        row = self.connection.execute(
            "SELECT * FROM scrapes WHERE server=? AND item=?", (server, item)
        ).fetchone()
        return (
            dict(row)
            if row and row["identity"] == identity and row["code"] == code
            else None
        )

    def save_record(
        self, server, item, identity, code, status, *, error=None, source=None
    ):
        old = self.record(server, item, identity, code)
        success = now() if status == "success" else old["succeeded_at"] if old else None
        source = source or (old["source"] if old else None)
        with self.connection:
            self.connection.execute(
                """INSERT INTO scrapes VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(server,item) DO UPDATE SET identity=excluded.identity,code=excluded.code,
                succeeded_at=excluded.succeeded_at,status=excluded.status,error=excluded.error,
                source=excluded.source,updated_at=excluded.updated_at""",
                (server, item, identity, code, success, status, error, source, now()),
            )

    def batch(self):
        row = self.connection.execute("SELECT data FROM batch WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_batch(self, batch):
        with self.connection:
            self.connection.execute(
                "INSERT INTO batch VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (json.dumps(batch, ensure_ascii=False),),
            )

    def cooldowns(self):
        return {
            r["id"]: json.loads(r["data"])
            for r in self.connection.execute("SELECT * FROM cooldowns")
        }

    def save_cooldown(self, source, value):
        with self.connection:
            self.connection.execute(
                "INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (str(source), json.dumps(value)),
            )


def read_rules(database: Path, kind: str, seed: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rules (kind TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO rules VALUES (?,?)", (kind, seed.read_text())
        )
        return json.loads(
            connection.execute(
                "SELECT data FROM rules WHERE kind=?", (kind,)
            ).fetchone()[0]
        )


def merge_rules(database: Path, kind: str, update):
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT data FROM rules WHERE kind=?", (kind,)
        ).fetchone()
        value = update(json.loads(row[0]))
        connection.execute(
            "UPDATE rules SET data=? WHERE kind=?",
            (json.dumps(value, ensure_ascii=False), kind),
        )
        return value
