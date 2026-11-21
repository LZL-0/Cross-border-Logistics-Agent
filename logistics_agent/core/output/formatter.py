# -*- coding: utf-8 -*-
"""输出格式化：统一为 {answer, trace, agent, meta}。"""

import json


def _safe(item):
    try:
        json.dumps(item, ensure_ascii=False)
        return item
    except TypeError:
        return str(item)


def build(answer: str, trace: list | None = None, agent: str = "", meta: dict | None = None) -> dict:
    result = {
        "answer": str(answer or "").strip(),
        "trace": [_safe(item) for item in (trace or [])],
        "agent": agent or "",
    }
    if meta:
        result["meta"] = _safe(meta)
    return result


def error(message: str, agent: str = "") -> dict:
    return build(f"处理失败：{message}", [], agent)


__all__ = ["build", "error"]
