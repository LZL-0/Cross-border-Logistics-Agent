# -*- coding: utf-8 -*-
"""反思：对一次回答做轻量自检，供前端展示。"""


def reflect(answer: str, trace: list | None = None) -> dict:
    trace = trace or []
    tools = [item for item in trace if isinstance(item, dict) and item.get("action") != "route"]
    failed = [item for item in tools if item.get("ok") is False]

    if not answer:
        note = "本轮没有产出回答，需检查模型或兜底函数。"
    elif failed:
        note = f"有 {len(failed)} 次工具调用被拒绝或失败，结论可能不完整。"
    elif not tools:
        note = "本轮未调用工具，回答来自规则/模板。"
    else:
        note = f"本轮调用 {len(tools)} 次工具，均已完成。"
    return {"tool_calls": len(tools), "failed": len(failed), "note": note}


__all__ = ["reflect"]
