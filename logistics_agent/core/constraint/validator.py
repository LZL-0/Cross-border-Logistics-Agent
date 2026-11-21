# -*- coding: utf-8 -*-
"""参数校验：工具参数里的路径不得逃出 TOOL_PATH_ROOTS。"""

import re
from pathlib import Path

from config import TOOL_PATH_ROOTS

_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_PATH_KEYS = ("path", "file", "filename", "dir", "root", "out_path", "export_path")


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _check_value(value) -> str:
    text = str(value)
    if ".." in text:
        return f"路径含上跳片段：{text}"
    looks_abs = bool(_WIN_ABS.match(text)) or text.startswith(("/", "\\"))
    if looks_abs:
        resolved = Path(text).expanduser().resolve()
        if not _inside(resolved, TOOL_PATH_ROOTS):
            return f"路径越界：{text}"
    return ""


def check_paths(args) -> tuple[bool, str]:
    """递归扫描参数，命中越界路径时返回 (False, 原因)。"""
    if not isinstance(args, dict):
        return True, ""

    for key, value in args.items():
        if isinstance(value, dict):
            ok, reason = check_paths(value)
            if not ok:
                return False, reason
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    ok, reason = check_paths(item)
                    if not ok:
                        return False, reason
                elif isinstance(item, str) and key in _PATH_KEYS:
                    reason = _check_value(item)
                    if reason:
                        return False, reason
        elif isinstance(value, str) and key in _PATH_KEYS:
            reason = _check_value(value)
            if reason:
                return False, reason
    return True, ""


__all__ = ["check_paths"]
