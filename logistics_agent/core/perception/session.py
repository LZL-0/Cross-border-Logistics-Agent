# -*- coding: utf-8 -*-
"""会话识别：没有 session_id 时生成一个短 ID。"""

import re
import uuid

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def ensure_session(session_id: str | None = None) -> str:
    sid = _SAFE_RE.sub("", str(session_id or "").strip())[:64]
    if sid:
        return sid
    return uuid.uuid4().hex[:12]


__all__ = ["ensure_session"]
