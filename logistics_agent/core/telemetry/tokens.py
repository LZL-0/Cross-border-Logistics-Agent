# -*- coding: utf-8 -*-
"""Token 计量：优先取模型返回的 usage，缺失时按字符估算。

估算口径（仅在接口没返回 usage 时使用）：
- 中日韩汉字：1 字 ≈ 1 token
- 其他字符：4 字符 ≈ 1 token
结果会标记 estimated=True，面板上可与真实用量区分。
"""

import math
import re

_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def estimate(text: str) -> int:
    """按字符粗估 token 数。"""
    text = str(text or "")
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


def _field(usage, *names: str) -> int:
    """依次尝试多个字段名，兼容 OpenAI / DeepSeek 等不同 usage 结构。"""
    extra = getattr(usage, "model_extra", None) or {}
    for name in names:
        value = getattr(usage, name, None)
        if value is None:
            value = extra.get(name)
        if value:
            return int(value)
    return 0


def read_usage(resp) -> dict:
    """从 chat.completions 响应里取 usage；没有时返回 0 并标记 estimated。

    缓存命中字段各家用名不同：
    - OpenAI：usage.prompt_tokens_details.cached_tokens
    - DeepSeek：usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
    """
    out = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "estimated": True}
    usage = getattr(resp, "usage", None)
    if usage is None:
        return out
    out["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
    out["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    if not cached:
        cached = _field(usage, "prompt_cache_hit_tokens")
    if not cached:
        miss = _field(usage, "prompt_cache_miss_tokens")
        if miss and out["prompt_tokens"] > miss:
            cached = out["prompt_tokens"] - miss
    out["cached_tokens"] = max(0, cached)
    out["estimated"] = not (out["prompt_tokens"] or out["completion_tokens"])
    return out


__all__ = ["estimate", "read_usage"]
