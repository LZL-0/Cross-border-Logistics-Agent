# -*- coding: utf-8 -*-
"""报价单 CSV 读写：追加、读取、统计，带简化文件锁。

所有路径均相对项目根，不写绝对路径。
"""

import csv
import os
import time
from contextlib import contextmanager
from pathlib import Path

from config import PATHS

QUOTE_FIELDS = [
    "quote_id",
    "created_at",
    "country",
    "mode",
    "carrier",
    "weight_kg",
    "declared_value",
    "freight_total",
    "tax",
    "landed_total",
    "eta_date",
    "note",
]


@contextmanager
def file_lock(path: Path, timeout: float = 3.0):
    """简化版文件锁：用同名 .lock 文件做进程间互斥。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(path) + ".lock")
    deadline = time.time() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() > deadline:
                try:
                    lock.unlink()
                except OSError:
                    pass
                deadline = time.time() + timeout
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def read_csv(path: Path, fields: list[str] | None = None) -> list[dict]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with file_lock(path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = [dict(row) for row in csv.DictReader(f)]
    if fields:
        return [{k: row.get(k, "") for k in fields} for row in rows]
    return rows


def append_rows(path: Path, rows: list[dict], fields: list[str] | None = None) -> int:
    path = Path(path)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    is_empty = (not path.exists()) or path.stat().st_size == 0
    cols = fields or list(rows[0].keys())
    with file_lock(path):
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            if is_empty:
                writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in cols})
    return len(rows)


def count_rows(path: Path) -> int:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with file_lock(path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            total = sum(1 for _ in reader)
    return max(0, total - 1)


def quote_rows() -> list[dict]:
    return read_csv(PATHS["quote_log"], QUOTE_FIELDS)


__all__ = ["QUOTE_FIELDS", "file_lock", "read_csv", "append_rows", "count_rows", "quote_rows"]
