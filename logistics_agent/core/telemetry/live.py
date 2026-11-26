# -*- coding: utf-8 -*-
"""遥测：把思考与工具调用写入 data/telemetry/live.jsonl。"""

import json
import time

from config import PATHS


def log(kind: str, **data) -> dict:
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
    record.update(data)
    path = PATHS["telemetry_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record


def think(text: str, **data) -> dict:
    return log("think", text=str(text)[:1200], **data)


def tool(name: str, args, ok: bool, result, **data) -> dict:
    return log(
        "tool",
        name=name,
        args=str(args)[:600],
        ok=bool(ok),
        result=str(result)[:1200],
        **data,
    )


def usage(**data) -> dict:
    """一次问答的 token 消耗（多轮 ReAct 已累加）。"""
    return log("usage", **data)


__all__ = ["log", "think", "tool", "usage"]
