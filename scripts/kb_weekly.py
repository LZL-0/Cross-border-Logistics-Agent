# -*- coding: utf-8 -*-
"""无服务场景下的周更入口：只生成周报（不写盘），供 Windows 计划任务调用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from logistics_agent.core.kb_sync import build_proposal  # noqa: E402

if __name__ == "__main__":
    proposal = build_proposal("windows-task")
    print(
        f"stamp={proposal['stamp']} ports={proposal['ports']['count']} "
        f"market={'ok' if proposal['market']['proposed'] else 'fail'} report={proposal['report']}"
    )
