# -*- coding: utf-8 -*-
"""记忆工具：供智能体在推理过程中主动调取记忆。

只读，不写入；短期记忆按会话隔离，智能体拿不到 session_id 时走长期记忆。
"""

from logistics_agent.core.toolkit.decorator import register
from logistics_agent.memory import long_term


@register(roles=["*"], name="memory.search")
def search(query: str = "", n: int = 4) -> dict:
    """检索长期记忆：按关键词返回历史问答结论与偏好条目。"""
    items = long_term.search(query, n)
    return {
        "count": len(items),
        "items": [{"ts": r.get("ts", ""), "text": r.get("text", ""), "tags": r.get("tags", [])} for r in items],
    }


@register(roles=["*"], name="memory.recent")
def recent(n: int = 4) -> dict:
    """读取最近写入的长期记忆条目（最新的在前）。"""
    items = long_term.all_entries(int(n))[::-1]
    return {
        "count": len(items),
        "items": [{"ts": r.get("ts", ""), "text": r.get("text", ""), "tags": r.get("tags", [])} for r in items],
    }


__all__ = ["search", "recent"]
