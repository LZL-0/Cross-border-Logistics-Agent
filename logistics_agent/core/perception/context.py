# -*- coding: utf-8 -*-
"""上下文组装：系统提示 + 死规则 + 记忆摘要，按字符预算分配。

注意：用户输入不再重复塞进 system 上下文，由 user message 承载全文；
避免同一段文字在提示里出现两遍、并挤占预算。
"""

from config import CONTEXT_CHAR_BUDGET


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n…（已截断）"


def build_context(
    system_prompt: str,
    rules: str,
    memory_text: str,
    budget: int | None = None,
) -> str:
    """三段式上下文；总长度不超过 budget。"""
    total = int(budget or CONTEXT_CHAR_BUDGET)
    system_part = _clip(system_prompt, int(total * 0.45))
    rules_part = _clip(rules, int(total * 0.25))
    memory_part = _clip(memory_text, int(total * 0.30))

    blocks = [
        "【系统提示】\n" + (system_part or "（无）"),
        "【死规则】\n" + (rules_part or "（无）"),
        "【记忆摘要】\n" + (memory_part or "（无）"),
    ]
    return "\n\n".join(blocks)


__all__ = ["build_context"]
