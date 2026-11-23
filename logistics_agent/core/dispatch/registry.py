# -*- coding: utf-8 -*-
"""工具注册表：按角色发放工具，并做角色越权校验。"""

from config import AGENT_PERMISSION_MODE, TOOL_NET_POLICY
from logistics_agent.core.toolkit.decorator import REGISTRY

# 业务角色（Supervisor 不持工具）
ALL_TOOL_ROLES = ("plan", "quote", "clearance", "compliance")

# 工具 ↔ 角色矩阵（文档镜像，新增工具时必须同步 struct.md）
TOOL_ROLES = {
    "document.parse_document": ("*",),
    "knowledge.lookup_hs": ("compliance", "quote", "plan"),
    "knowledge.duty_calc": ("compliance", "quote", "plan"),
    "knowledge.lookup_country": ("clearance", "compliance", "plan"),
    "clearance.estimate_clearance": ("clearance", "plan"),
    "clearance.port_status": ("clearance", "plan"),
    "rates.list_carriers": ("quote", "plan"),
    "rates.quote_rates": ("quote", "plan"),
    "rates.compare_rates": ("quote", "plan"),
    "shipment.plan_shipment": ("plan",),
    "shipment.export_quote": ("quote", "plan"),
    "shipment.quote_stats": ("quote", "plan"),
}

_LOADED = False


def load_tools() -> None:
    """导入所有工具模块，触发 @register 完成登记（幂等）。"""
    global _LOADED
    if _LOADED:
        return
    from logistics_agent.tools import (  # noqa: F401  导入即注册
        clearance,
        document,
        knowledge,
        rates,
        shipment,
    )
    from logistics_agent.memory import tools as memory_tools  # noqa: F401  导入即注册记忆工具

    _LOADED = True


class GlobalRegistry:
    """工具注册表的只读视图。"""

    def tools_for(self, role: str) -> dict[str, dict]:
        load_tools()
        if not role:
            return {}
        result: dict[str, dict] = {}
        for name, item in REGISTRY.items():
            if "*" in item["roles"] or role in item["roles"]:
                result[name] = {
                    "func": item["func"],
                    "description": item["description"],
                    "roles": list(item["roles"]),
                }
        return result

    def get(self, name: str) -> dict | None:
        load_tools()
        return REGISTRY.get(name)

    def allows(self, role: str, name: str) -> tuple[bool, str]:
        """运行时角色校验：工具是否属于该角色（越权直接拒绝）。"""
        tool = self.get(name)
        if tool is None:
            return False, f"未知工具：{name}"
        roles = tool.get("roles", [])
        if "*" in roles or role in roles:
            return True, ""
        return False, f"工具 {name} 不属于角色 {role}（可用角色：{', '.join(roles)}）"

    def names(self) -> list[str]:
        load_tools()
        return sorted(REGISTRY)

    def policy(self) -> dict:
        return {"permission_mode": AGENT_PERMISSION_MODE, "net_policy": TOOL_NET_POLICY}


GLOBAL_REGISTRY = GlobalRegistry()

__all__ = ["GLOBAL_REGISTRY", "TOOL_ROLES", "ALL_TOOL_ROLES", "load_tools"]
