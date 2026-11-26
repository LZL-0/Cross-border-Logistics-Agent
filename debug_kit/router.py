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


@router.get("/knowledge_file")
def knowledge_file(name: str) -> dict:
    """知识库文件全文：name 仅允许 data/knowledge 下的 .json 文件名。"""
    return {"ok": True, "item": reader.knowledge_file(name)}


def _signature(fn) -> str:
    """把一个工具函数渲染成 name(a, b=1) 形式的对外接口签名。"""
    if fn is None:
        return ""
    params = ", ".join(
        f"{p.name}={p.default!r}" if p.default is not inspect.Parameter.empty else p.name
        for p in inspect.signature(fn).parameters.values()
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    )
    return params


@router.get("/tools_meta")
def tools_meta() -> dict:
    """已注册工具的说明与对外接口（名称 + 参数签名 + 角色），供细节页展示。"""
    load_tools()
    items = {}
    for name, item in sorted(REGISTRY.items()):
        params = _signature(item.get("func"))
        items[name] = {
            "name": name,
            "description": item.get("description", ""),
            "params": params,
            "signature": f"{name}({params})",
            "roles": list(item.get("roles", [])),
        }
    return {"ok": True, "count": len(items), "items": items}


@router.get("/agents_meta")
def agents_meta() -> dict:
    """智能体清单：角色、类名、自述，以及该角色被授权使用的工具接口。"""
    from logistics_agent.Agents import (
        ClearanceAgent,
        ComplianceAgent,
        PlanAgent,
        QuoteAgent,
        SummarizerAgent,
    )

    load_tools()
    classes = [QuoteAgent, PlanAgent, ClearanceAgent, ComplianceAgent, SummarizerAgent]
    items = {}
    for cls in classes:
        role = getattr(cls, "role", "")
        tools = []
        for name, item in sorted(REGISTRY.items()):
            roles = item.get("roles", [])
            if "*" in roles or role in roles:
                params = _signature(item.get("func"))
                tools.append(
                    {
                        "name": name,
                        "signature": f"{name}({params})",
                        "description": item.get("description", ""),
                    }
                )
        items[role] = {
            "role": role,
            "name": cls.__name__,
            "description": getattr(cls, "system_prompt", ""),
            "tools": tools,
        }
    items["supervisor"] = {
        "role": "supervisor",
        "name": "Supervisor",
        "description": "规则路由：按关键词把问题分给报价 / 方案 / 清关 / 合规 / 总结智能体，自身不调用工具。",
        "tools": [],
    }
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


@router.get("/usage")
def usage(q: str = "", session: str = "", limit: int = 50) -> dict:
    """问答级 token 消耗：items 为逐条记录，data 为累计汇总。"""
    items = reader.usage(q=q, session=session, limit=limit)
    return {"ok": True, "count": len(items), "data": reader.usage_total(), "items": items}


@router.get("/usage_call")
def usage_call(ts: str, rnd: int = 0) -> dict:
    """分对话细节：某次提问中第 rnd 次模型请求的工具/耗时/内容/报错。"""
    return {"ok": True, "item": reader.usage_call(ts, rnd)}


@router.get("/traces")
def traces(limit: int = 20) -> dict:
    """内存事件（本次进程的请求级记录）。"""
    return {"ok": True, "items": recorder.recent(limit)}


@router.post("/clear")
def clear() -> dict:
    recorder.clear()
    return {"ok": True}
