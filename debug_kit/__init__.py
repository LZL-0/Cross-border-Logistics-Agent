# -*- coding: utf-8 -*-
"""调试平面（最小骨架）。

设计原则：
1. 与业务代码物理隔离，整个目录可删、可构建期剔除；
2. 只有 DEBUG_TOOLKIT=1 时才挂载路由、注册调试工具；
3. 调试工具归属 debugger 角色，普通用户会话调用会被权限闸拒绝。

当前只包含 P0 骨架：事件记录 + 两个只读接口 + 一个页面。
"""

from debug_kit.flags import DEBUG

__all__ = ["DEBUG"]
