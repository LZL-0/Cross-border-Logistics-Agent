# -*- coding: utf-8 -*-
"""调试开关：只有显式打开时才加载调试能力。

用法：
    set DEBUG_TOOLKIT=1 && uvicorn server:app

正式包构建时把 DEBUG_TOOLKIT 置 0 并直接删除 debug_kit/ 目录。
"""

import os

DEBUG: bool = os.getenv("DEBUG_TOOLKIT", "0").strip() in {"1", "true", "True", "yes"}

# 环形缓冲长度：手机上够看最近几十次请求
BUFFER_SIZE: int = int(os.getenv("DEBUG_BUFFER_SIZE", "50"))

__all__ = ["DEBUG", "BUFFER_SIZE"]
