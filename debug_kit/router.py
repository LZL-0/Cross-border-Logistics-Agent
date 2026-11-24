# -*- coding: utf-8 -*-
"""调试接口：独立 APIRouter，仅在 DEBUG 时由 server.py 挂载。

全部只读（除 /clear），返回结构统一为 {ok, items|data}。
"""

import inspect

from fastapi import APIRouter

from debug_kit import reader, recorder
from debug_kit.flags import BUFFER_SIZE
from logistics_agent.core.toolkit.decorator import REGISTRY
from logistics_agent.core.dispatch.registry import load_tools

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/health")
def health() -> dict:
    return {"debug": True, "buffer_size": BUFFER_SIZE}


@router.get("/overview")
def overview() -> dict:
    return {"ok": True, "data": reader.overview()}


@router.get("/sessions")
def sessions() -> dict:
    return {"ok": True, "items": reader.sessions()}


@router.get("/tools")
def tools(q: str = "", session: str = "", limit: int = 50, errors: int = 0) -> dict:
    """工具调用记录：关键字 q 会匹配工具名/入参/出参/报错。"""
    items = reader.tool_calls(q=q, session=session, limit=limit, only_error=bool(errors))
    return {"ok": True, "count": len(items), "items": items}


@router.get("/short")
def short(q: str = "", session: str = "", limit: int = 50) -> dict:
    items = reader.short_term(q=q, session=session, limit=limit)
    return {"ok": True, "count": len(items), "items": items}


@router.get("/long")
def long(q: str = "", session: str = "", limit: int = 50) -> dict:
    items = reader.long_term_entries(q=q, session=session, limit=limit)
    return {"ok": True, "count": len(items), "items": items}


@router.get("/knowledge")
def knowledge(q: str = "") -> dict:
    items = reader.knowledge(q=q)
    return {"ok": True, "count": len(items), "items": items}


@router.get("/memory_tools")
def memory_tools(q: str = "", session: str = "", limit: int = 20) -> dict:
    """记忆工具：可用记忆工具清单 + 最近的真实调用记录。"""
    load_tools()
    available = []
    for name, item in sorted(REGISTRY.items()):
        if not name.startswith("memory."):
            continue
        params = ", ".join(
            f"{p.name}={p.default!r}" if p.default is not inspect.Parameter.empty else p.name
            for p in inspect.signature(item["func"]).parameters.values()
        )
        available.append(
            {
                "name": name,
                "roles": list(item.get("roles", [])),
                "description": item.get("description", ""),
                "params": params,
            }
        )
    items = [
        r
        for r in reader.tool_calls(q=q, session=session, limit=500)
        if str(r.get("name", "")).startswith("memory.")
    ][:limit]
    return {"ok": True, "count": len(items), "available": available, "items": items}


@router.get("/traces")
def traces(limit: int = 20) -> dict:
    """内存事件（本次进程的请求级记录）。"""
    return {"ok": True, "items": recorder.recent(limit)}


@router.post("/clear")
def clear() -> dict:
    recorder.clear()
    return {"ok": True}
