# -*- coding: utf-8 -*-
"""记忆访问权限门：L0 / L1 / L2 三级简化实现。

- L0：公开信息（税则、公开运价基线），任何模式可读；
- L1：项目内数据（报价记录、会话记忆），readonly 及以上可读；
- L2：写操作 / 导出报价单，plan 模式需显式 confirm，readonly 一律拒绝。
"""

from config import AGENT_PERMISSION_MODE

LEVELS = {
    "L0": "公开信息读取",
    "L1": "项目内数据读取",
    "L2": "写操作与报价导出",
}


def check(level: str, project_id: str | None = None) -> tuple[bool, str]:
    """返回 (是否放行, 原因)。project_id 目前只做占位，用于将来多项目隔离。"""
    level = (level or "L0").upper()
    mode = AGENT_PERMISSION_MODE

    if level not in LEVELS:
        return False, f"未知权限等级：{level}"
    if mode == "readonly":
        if level == "L0":
            return True, "readonly：允许公开读取"
        return False, f"readonly：禁止 {level}（{LEVELS[level]}）"
    if mode == "plan" and level == "L2":
        return True, "plan：L2 需调用方 confirm=true"

    if project_id is not None and not str(project_id).strip():
        return False, "project_id 为空"

    return True, f"{mode}：允许 {level}（{LEVELS[level]}）"


__all__ = ["LEVELS", "check"]
