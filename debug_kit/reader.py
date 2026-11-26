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


def knowledge_file(name: str):
    """读取单个知识库文件的完整内容（仅限 data/knowledge 下的 .json）。"""
    base = PATHS["knowledge"]
    fname = Path(str(name or "")).name
    if not fname.endswith(".json"):
        return None
    path = base / fname
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"file": fname, "error": str(exc)}


def usage(q: str = "", session: str = "", limit: int = 50) -> list[dict]:
    """总对话级 token：一次问答一条，calls 内为该轮每次模型请求（分对话）。"""
    rows = [r for r in _read_jsonl(PATHS["telemetry_file"]) if r.get("kind") == "usage"]
    out = []
    for r in reversed(rows):
        if session and r.get("session") != session:
            continue
        if not _hit(r, q):
            continue
        calls = r.get("calls") or []
        out.append(
            {
                "ts": r.get("ts", ""),
                "model": r.get("model", ""),
                "agent": r.get("agent", ""),
                "session": r.get("session", ""),
                "rounds": r.get("rounds", 0) or len(calls),
                "requests": r.get("requests", len(calls)),
                "calls": [
                    {
                        "round": c.get("round", 0),
                        "model": c.get("model", ""),
                        "latency_ms": c.get("latency_ms", 0),
                        "prompt_tokens": c.get("prompt_tokens", 0),
                        "completion_tokens": c.get("completion_tokens", 0),
                        "cached_tokens": c.get("cached_tokens", 0),
                        "total_tokens": c.get("total_tokens", 0),
                        "estimated": bool(c.get("estimated")),
                        "output": c.get("output", ""),
                        "tool_count": len(c.get("tools") or []),
                        "error_count": len(c.get("issues") or []),
                    }
                    for c in calls
                ],
                "prompt_tokens": r.get("prompt_tokens", 0),
                "completion_tokens": r.get("completion_tokens", 0),
                "total_tokens": r.get("total_tokens", 0),
                "cached_tokens": r.get("cached_tokens", 0),
                "estimated": bool(r.get("estimated")),
                "latency_ms": r.get("latency_ms", 0),
                "question": r.get("question", ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def usage_total() -> dict:
    """全部对话 + 全部模型请求的汇总。"""
    rows = [r for r in _read_jsonl(PATHS["telemetry_file"]) if r.get("kind") == "usage"]
    all_calls = [c for r in rows for c in (r.get("calls") or [])]
    total = {
        "calls": len(rows),
        "requests": len(all_calls) or sum(int(r.get("rounds", 0) or 0) for r in rows),
        "prompt_tokens": sum(int(r.get("prompt_tokens", 0) or 0) for r in rows),
        "completion_tokens": sum(int(r.get("completion_tokens", 0) or 0) for r in rows),
        "cached_tokens": sum(int(r.get("cached_tokens", 0) or 0) for r in rows),
        "estimated_calls": sum(1 for r in rows if r.get("estimated")),
    }
    total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]
    total["avg_request_ms"] = (
        round(sum(int(c.get("latency_ms", 0) or 0) for c in all_calls) / len(all_calls))
        if all_calls
        else 0
    )
    return total


def usage_call(ts: str, rnd: int = 0) -> dict | None:
    """分对话详情：某次提问里第 rnd 次模型请求的完整记录（工具 / 内容 / 报错）。"""
    for r in _read_jsonl(PATHS["telemetry_file"]):
        if r.get("kind") != "usage" or r.get("ts") != ts:
            continue
        calls = r.get("calls") or []
        for c in calls:
            if rnd and int(c.get("round", 0) or 0) != int(rnd):
                continue
            return {
                "turn": {
                    "ts": r.get("ts", ""),
                    "agent": r.get("agent", ""),
                    "model": r.get("model", ""),
                    "session": r.get("session", ""),
                    "question": r.get("question", ""),
                    "total_tokens": r.get("total_tokens", 0),
                    "requests": r.get("requests", len(calls)),
                    "latency_ms": r.get("latency_ms", 0),
                    "error": r.get("error", ""),
                },
                "call": c,
            }
        return None
    return None


def overview() -> dict:
    """面板顶部概览数字。"""
    tools = [r for r in _read_jsonl(PATHS["telemetry_file"]) if r.get("kind") == "tool"]
    tok = usage_total()
    return {
        "tool_calls": len(tools),
        "tool_errors": sum(1 for r in tools if not r.get("ok")),
        "sessions": len(sessions()),
        "short_messages": len(_read_jsonl(PATHS["short_term"])),
        "long_entries": len(long_term.all_entries(100000)),
        "knowledge_files": len(list(PATHS["knowledge"].glob("*.json"))),
        "llm_calls": tok["calls"],
        "tokens_total": tok["total_tokens"],
    }


__all__ = [
    "tool_calls",
    "short_term",
    "long_term_entries",
    "knowledge",
    "knowledge_file",
    "sessions",
    "overview",
    "usage",
    "usage_total",
    "usage_call",
]
