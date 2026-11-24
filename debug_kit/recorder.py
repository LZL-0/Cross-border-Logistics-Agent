# -*- coding: utf-8 -*-
"""事件记录：内存环形缓冲，重启即清，不落盘。

后续可在此加 trace 树、采样与脱敏，接口保持不变。
"""

import threading
import time
from collections import deque

from debug_kit.flags import BUFFER_SIZE

_BUFFER: deque = deque(maxlen=BUFFER_SIZE)
_LOCK = threading.Lock()


def record(kind: str, **fields) -> None:
    """记录一条调试事件，如 record("tool", agent="quote", name="rates.compare_rates")。"""
    with _LOCK:
        _BUFFER.append({"ts": round(time.time(), 3), "kind": kind, **fields})


def recent(limit: int = 20) -> list[dict]:
    """取最近 limit 条事件（新→旧）。"""
    with _LOCK:
        items = list(_BUFFER)
    return list(reversed(items[-limit:]))


def clear() -> None:
    with _LOCK:
        _BUFFER.clear()


__all__ = ["record", "recent", "clear"]
