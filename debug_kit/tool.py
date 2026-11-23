# -*- coding: utf-8 -*-
"""调试工具：debugger 角色专用，只读，供智能体自查。

普通角色（quote/plan/clearance/compliance）调用会被 GlobalRegistry.allows 拒绝。
"""

from debug_kit import reader
from logistics_agent.core.toolkit.decorator import register


@register(roles=["debugger"], name="debug.tool_calls")
def tool_calls(q: str = "", session: str = "", limit: int = 10, errors_only: bool = False) -> dict:
    """查看最近工具调用：时间、入参、出参、是否报错。"""
    items = reader.tool_calls(q=q, session=session, limit=limit, only_error=errors_only)
    return {"count": len(items), "items": items}


@register(roles=["debugger"], name="debug.memory")
def memory(scope: str = "short", q: str = "", session: str = "", limit: int = 10) -> dict:
    """查看记忆：scope=short 短期对话，scope=long 长期条目。"""
    if scope == "long":
        items = reader.long_term_entries(q=q, session=session, limit=limit)
    elif scope == "knowledge":
        items = reader.knowledge(q=q)
    else:
        items = reader.short_term(q=q, session=session, limit=limit)
    return {"scope": scope, "count": len(items), "items": items}


@register(roles=["debugger"], name="debug.overview")
def overview() -> dict:
    """调试概览：工具调用数、报错数、会话数、记忆与知识库规模。"""
    return reader.overview()


__all__ = ["tool_calls", "memory", "overview"]
