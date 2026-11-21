# -*- coding: utf-8 -*-
"""长期记忆：追加 jsonl，按关键词做轻量检索。

检索按 session_id 隔离：优先同会话条目，其次全局偏好类条目（tag=preference）。
"""

import json
import re
import time
from pathlib import Path

from config import PATHS

_SPLIT_RE = re.compile(r"[\s,，、;；:：/|]+")


def _path() -> Path:
    path = PATHS["long_term"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def add(text: str, tags: list[str] | None = None, session_id: str = "") -> dict:
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "text": str(text)[:1000],
        "tags": list(tags or []),
    }
    with open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _keywords(query: str) -> list[str]:
    words = [w.lower() for w in _SPLIT_RE.split(query or "") if w.strip()]
    grams: list[str] = []
    for word in words:
        if len(word) >= 2 and re.search(r"[\u4e00-\u9fff]", word):
            grams.extend(word[i : i + 2] for i in range(len(word) - 1))
    return list(dict.fromkeys(words + grams))


def search(query: str, n: int = 5, session_id: str | None = None) -> list[dict]:
    """关键词匹配打分；session_id 给定时只返回本会话 + 全局偏好条目。"""
    path = _path()
    if not path.exists():
        return []
    keys = _keywords(query)
    scored: list[tuple[int, dict]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if session_id and rec.get("session_id") not in (session_id, ""):
                if "preference" not in (rec.get("tags") or []):
                    continue
            haystack = (rec.get("text", "") + " " + " ".join(rec.get("tags", []))).lower()
            score = sum(1 for key in keys if key and key in haystack)
            if score > 0 or not keys:
                scored.append((score, rec))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [rec for _, rec in scored[: max(1, int(n))]]


def all_entries(limit: int = 50) -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-max(1, int(limit)):]


__all__ = ["add", "search", "all_entries"]
