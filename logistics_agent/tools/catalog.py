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


def _first_line(path: Path) -> str:
    """返回第一行非空文本（去掉 BOM），文件不存在或只有 BOM 时返回空串。"""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return ""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for line in f:
            text = line.strip().lstrip("\ufeff")
            if text:
                return text
    return ""


def _header_ok(path: Path, cols: list[str]) -> bool:
    return _first_line(path).replace(" ", "") == ",".join(cols)


def _split_header(path: Path, cols: list[str]) -> tuple[bool, list[list[str]]]:
    """读取全部行，返回 (首行是否为表头, 数据行)。兼容无表头/BOM-only 的坏文件。"""
    if not path.exists() or path.stat().st_size == 0:
        return True, []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [row for row in csv.reader(f) if row and any(cell.strip() for cell in row)]
    if not rows:
        return True, []
    if rows[0][0].strip().lstrip("\ufeff") == cols[0]:
        return True, rows[1:]
    return False, rows


def _rewrite(path: Path, cols: list[str], rows: list[list[str]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)
    os.replace(tmp, path)


def _clean(value) -> str:
    """写盘前清洗：防 Excel 公式注入（= + - @ 开头）。"""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@"):
        text = "'" + text
    return text


def read_csv(path: Path, fields: list[str] | None = None) -> list[dict]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with file_lock(path):
        if fields and not _header_ok(path, fields):
            _has_header, rows_all = _split_header(path, fields)
            _rewrite(path, fields, rows_all)
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
    cols = fields or list(rows[0].keys())
    with file_lock(path):
        if not _header_ok(path, cols):
            _has_header, old_rows = _split_header(path, cols)
            _rewrite(path, cols, old_rows)
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            for row in rows:
                writer.writerow({k: _clean(row.get(k, "")) for k in cols})
    return len(rows)


def count_rows(path: Path) -> int:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with file_lock(path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.reader(f) if row and any(c.strip() for c in row)]
    if not rows:
        return 0
    return len(rows) - 1


def quote_rows() -> list[dict]:
    return read_csv(PATHS["quote_log"], QUOTE_FIELDS)


__all__ = ["QUOTE_FIELDS", "file_lock", "read_csv", "append_rows", "count_rows", "quote_rows"]
