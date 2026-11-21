# -*- coding: utf-8 -*-
"""orchestrator：一次请求的编排薄层。

只做两件事：生成/校验会话、把请求交给 Supervisor，并写入遥测。
业务逻辑一律不放在这里。
"""

from logistics_agent.core.perception.session import ensure_session
from logistics_agent.core.telemetry import live


class Orchestrator:
    def __init__(self, supervisor):
        self.supervisor = supervisor

    def handle(self, text: str, session_id: str | None = None) -> dict:
        sid = ensure_session(session_id)
        live.log("request", session=sid, text=str(text)[:300])
        result = self.supervisor.ask(text, sid)
        live.log(
            "response",
            session=sid,
            agent=result.get("agent", ""),
            chars=len(result.get("answer", "")),
            tools=len(result.get("trace", [])),
        )
        return result


__all__ = ["Orchestrator"]
