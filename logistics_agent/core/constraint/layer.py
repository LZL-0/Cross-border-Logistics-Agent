# -*- coding: utf-8 -*-
"""约束层入口：四道检查（权限 / 路径 / 网络 / 危险写）。

任何工具调用都必须先过这里，顺序固定：
1. 权限门（L1 读 / L2 写）
2. 路径检查（不得逃出 TOOL_PATH_ROOTS）
3. 网络策略（扫描所有参数中的 URL，不只 url 字段）
4. 危险写操作（plan 模式需 confirm=true）
"""

from urllib.parse import urlparse

from config import AGENT_PERMISSION_MODE, TOOL_NET_ALLOWLIST, TOOL_NET_POLICY
from logistics_agent.memory.access import check as access_check
from logistics_agent.core.constraint import budget, validator
from logistics_agent.core.constraint.rules import DANGEROUS_WRITE, NET_TOOLS, WRITE_TOOLS


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _iter_urls(args):
    """递归找出参数里所有 http(s) 字符串，避免只查 args['urls'] 的盲区。"""
    if isinstance(args, dict):
        for value in args.values():
            yield from _iter_urls(value)
    elif isinstance(args, (list, tuple)):
        for item in args:
            yield from _iter_urls(item)
    elif isinstance(args, str) and args.strip().lower().startswith(("http://", "https://")):
        yield args.strip()


def _host_allowed(host: str, allowlist) -> bool:
    """白名单匹配：`*` 全放行；`*.x.com` 前缀通配；`.x.com` 域名及其子域；其余精确匹配。"""
    for item in allowlist:
        item = item.strip().lower()
        if not item:
            continue
        if item == "*":
            return True
        if item.startswith("*."):
            base = item[2:]
            if host == base or host.endswith("." + base):
                return True
        elif item.startswith("."):
            if host == item[1:] or host.endswith(item):
                return True
        elif host == item:
            return True
    return False


def _check_network(args: dict) -> tuple[bool, str]:
    if TOOL_NET_POLICY == "block":
        return False, "网络策略为 block，禁止一切外网请求"

    for url in _iter_urls(args):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"仅允许 http/https：{url}"
        if TOOL_NET_ALLOWLIST:
            host = (parsed.hostname or "").lower()
            if not _host_allowed(host, TOOL_NET_ALLOWLIST):
                return False, f"域名不在白名单：{host}"
    return True, ""


def check_tool_call(name: str, args: dict | None = None) -> tuple[bool, str]:
    """返回 (是否放行, 说明)。"""
    args = args or {}

    # 第 1 道：权限
    level = "L2" if name in WRITE_TOOLS else "L1"
    ok, reason = access_check(level, project_id="default")
    if not ok:
        return False, f"权限拒绝：{reason}"

    # 第 2 道：路径
    ok, reason = validator.check_paths(args)
    if not ok:
        return False, f"路径拒绝：{reason}"

    # 第 3 道：网络（所有工具都检查参数里的 URL；离线项目通常无 URL 参数）
    ok, reason = _check_network(args)
    if not ok:
        return False, f"网络拒绝：{reason}"

    # 第 4 道：危险写操作
    if AGENT_PERMISSION_MODE == "plan" and name in DANGEROUS_WRITE:
        if not _truthy(args.get("confirm", False)):
            return False, f"{name} 属于危险写操作，plan 模式需 confirm=true"

    return True, "通过四道检查"


def check_budget(session_id: str) -> tuple[bool, str]:
    return budget.consume(session_id)


__all__ = ["check_tool_call", "check_budget"]
