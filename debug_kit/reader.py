# -*- coding: utf-8 -*-
"""只读数据源：把遥测与记忆文件读成调试面板要的结构。

所有函数只读文件，不写、不改业务状态；关键字过滤统一在这里做。
"""

import json
from pathlib import Path

from config import PATHS
from logistics_agent.memory import long_term


def _read_jsonl(path: Path, max_lines: int = 2000) -> list[dict]:
    """读取 jsonl 尾部最多 max_lines 行，坏行跳过。"""
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
    return rows[-max_lines:]


def _hit(row: dict, q: str) -> bool:
    if not q:
        return True
    return q.lower() in json.dumps(row, ensure_ascii=False).lower()


def tool_calls(q: str = "", session: str = "", limit: int = 50, only_error: bool = False) -> list[dict]:
    """工具调用记录：时间戳、工具名、入参、出参、是否报错、会话。"""
    rows = [r for r in _read_jsonl(PATHS["telemetry_file"]) if r.get("kind") == "tool"]
    out = []
    for r in reversed(rows):
        if session and r.get("session") != session:
            continue
        if only_error and r.get("ok"):
            continue
        if not _hit(r, q):
            continue
        out.append(
            {
                "ts": r.get("ts", ""),
                "name": r.get("name", ""),
                "args": r.get("args", ""),
                "result": r.get("result", ""),
                "ok": bool(r.get("ok")),
                "stage": r.get("stage", ""),
                "session": r.get("session", ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def short_term(q: str = "", session: str = "", limit: int = 50) -> list[dict]:
    """短期记忆：按会话的对话轮次。"""
    rows = _read_jsonl(PATHS["short_term"])
    out = [
        r
        for r in reversed(rows)
        if (not session or r.get("session_id") == session) and _hit(r, q)
    ]
    return out[:limit]


def sessions() -> list[dict]:
    """会话清单：消息数 + 最后时间，供面板下拉。"""
    rows = _read_jsonl(PATHS["short_term"])
    agg: dict[str, dict] = {}
    for r in rows:
        sid = r.get("session_id") or "-"
        item = agg.setdefault(sid, {"session_id": sid, "messages": 0, "last": ""})
        item["messages"] += 1
        item["last"] = r.get("ts", item["last"])
    return sorted(agg.values(), key=lambda x: x["last"], reverse=True)


def long_term_entries(q: str = "", session: str = "", limit: int = 50) -> list[dict]:
    """长期记忆：偏好与结论条目。"""
    rows = long_term.all_entries(500)
    out = [
        r
        for r in reversed(rows)
        if (not session or r.get("session_id") in (session, "")) and _hit(r, q)
    ]
    return out[:limit]


def knowledge(q: str = "") -> list[dict]:
    """知识库文件体检：条目数与字段概况。"""
    out = []
    base = PATHS["knowledge"]
    for path in sorted(base.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            out.append({"file": path.name, "error": str(exc)})
            continue
        if not isinstance(data, dict):
            out.append({"file": path.name, "keys": 0, "items": len(data) if isinstance(data, list) else 0})
            continue
        lists = [v for k, v in data.items() if k != "_meta" and isinstance(v, list)]
        item = {
            "file": path.name,
            "keys": len(lists),
            "items": sum(len(v) for v in lists),
            "fields": len(lists[0][0]) if lists and lists[0] and isinstance(lists[0][0], dict) else 0,
            "meta": data.get("_meta", {}),
        }
        if _hit(item, q):
            out.append(item)
    return out


def overview() -> dict:
    """面板顶部概览数字。"""
    tools = [r for r in _read_jsonl(PATHS["telemetry_file"]) if r.get("kind") == "tool"]
    return {
        "tool_calls": len(tools),
        "tool_errors": sum(1 for r in tools if not r.get("ok")),
        "sessions": len(sessions()),
        "short_messages": len(_read_jsonl(PATHS["short_term"])),
        "long_entries": len(long_term.all_entries(100000)),
        "knowledge_files": len(list(PATHS["knowledge"].glob("*.json"))),
    }


__all__ = [
    "tool_calls",
    "short_term",
    "long_term_entries",
    "knowledge",
    "sessions",
    "overview",
]
