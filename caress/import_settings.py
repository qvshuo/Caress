"""One-shot import into a new database; never opens the old deployment for writing."""

import argparse
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from .runtime import Runtime
from .settings import Settings, SiteSettings, TranslationSettings


def import_settings(source: Path, destination: Path):
    if destination.exists():
        raise ValueError("目标已存在，请使用全新数据库路径")
    backup = source.with_name(
        source.name + "." + datetime.now().strftime("%Y%m%d%H%M%S%f") + ".backup"
    )
    rules = []
    if source.suffix.lower() == ".json":
        shutil.copy2(source, backup)
        data = json.loads(source.read_text())
    else:
        with (
            closing(
                sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
            ) as src,
            closing(sqlite3.connect(backup)) as dst,
        ):
            src.backup(dst)
            row = src.execute("SELECT data FROM options WHERE id=1").fetchone()
            data = json.loads(row[0]) if row else {}
            tables = {
                r[0]
                for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "rules" in tables:
                rules = src.execute("SELECT kind,data FROM rules").fetchall()
    data = {
        k: v for k, v in data.items() if k in Settings.model_fields and k != "database"
    }
    if "emby" in data:
        data["emby"] = {
            k: v
            for k, v in data["emby"].items()
            if k in ("url", "api_key", "library_ids")
        }
    data["sites"] = {
        name: {k: v for k, v in site.items() if k in SiteSettings.model_fields}
        for name, site in data.get("sites", {}).items()
    }
    if "translation" in data:
        data["translation"] = {
            k: v
            for k, v in data["translation"].items()
            if k in TranslationSettings.model_fields
        }
    settings = Settings.model_validate({**data, "database": destination})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".caress-import-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / "caress.sqlite3"
        runtime = Runtime(staged)
        try:
            with runtime.connection:
                settings.save(connection=runtime.connection)
                runtime.connection.execute(
                    "CREATE TABLE IF NOT EXISTS rules (kind TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
                for kind, value in rules:
                    if kind in ("actors", "catalog"):
                        from .actors import ActorAliases
                        from .catalog import CatalogRules

                        parsed = json.loads(value)
                        if kind == "actors":
                            ActorAliases(parsed["actors"])
                        else:
                            CatalogRules.model_validate(parsed)
                        runtime.connection.execute(
                            "INSERT INTO rules VALUES (?,?)", (kind, value)
                        )
        finally:
            runtime.close()
        # A hard link publishes atomically and fails if another process created the destination.
        os.link(staged, destination)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        backup = import_settings(args.source, args.destination)
    except ValueError, OSError, sqlite3.Error:
        parser.exit(
            1,
            "导入失败：检查原配置、匹配规则和路径权限；目标必须是新文件，原文件与备份保留。\n",
        )
    print(
        f"已备份原配置：{backup}\n已导入连接、来源、翻译和匹配规则。旧成功状态无法证明当前 Item 身份及完整写入，未迁移；首次批次前请核对目标作品。"
    )


if __name__ == "__main__":
    main()
