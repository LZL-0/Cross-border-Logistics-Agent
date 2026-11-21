# -*- coding: utf-8 -*-
"""主控智能体：规则路由，不调用 LLM、不持有工具。"""

import re

from logistics_agent.core.perception.input import clean_input

# 按顺序匹配，先命中者生效；全不中默认 quote
ROUTE_RULES = (
    ("plan", re.compile(r"全流程|一条龙|端到端|走一遍|方案|推荐|怎么发|帮我选|最优")),
    ("summarizer", re.compile(r"总结|汇总|复盘|统计|报价记录|历史")),
    ("quote", re.compile(r"比价|报价|运费|多少钱|便宜|成本|价格|对比|运价")),
    ("clearance", re.compile(r"清关|时效|多久|几天|查验|扣关|滞港|延误")),
    ("compliance", re.compile(r"HS|海关编码|归类|关税|增值税|税|认证|单证|合规|申报")),
)
DEFAULT_ROLE = "quote"


class Supervisor:
    """规则路由：把问题分给对应的业务智能体。"""

    role = "supervisor"

    def _route(self, text: str) -> str:
        for role, pattern in ROUTE_RULES:
            if pattern.search(text):
                return role
        return DEFAULT_ROLE

    def _agent(self, role: str):
        from logistics_agent.Agents.clearance_agent import ClearanceAgent
        from logistics_agent.Agents.compliance_agent import ComplianceAgent
        from logistics_agent.Agents.plan_agent import PlanAgent
        from logistics_agent.Agents.quote_agent import QuoteAgent
        from logistics_agent.Agents.summarizer import SummarizerAgent

        return {
            "plan": PlanAgent,
            "quote": QuoteAgent,
            "clearance": ClearanceAgent,
            "compliance": ComplianceAgent,
            "summarizer": SummarizerAgent,
        }.get(role, QuoteAgent)()

    def ask(self, text: str, session_id: str | None = None) -> dict:
        role = self._route(clean_input(text))
        result = self._agent(role).ask(text, session_id)
        result.setdefault("trace", [])
        result["trace"].insert(0, {"round": 0, "action": "route", "args": {}, "ok": True, "result": role})
        return result

    def stats(self) -> dict:
        """聚合看板数据（供 /api/stats 使用）。"""
        from config import AGENT_PERMISSION_MODE, LLM_MODEL, TOOL_NET_POLICY
        from logistics_agent.core.constraint import budget
        from logistics_agent.tools.catalog import quote_rows
        from logistics_agent.tools.knowledge import carriers_data, countries, hs_items, ports_data

        rows = quote_rows()
        return {
            "knowledge": {
                "countries": len(countries()),
                "hs": len(hs_items()),
                "carriers": len(carriers_data()),
                "ports": len(ports_data()),
            },
            "quotes": {"total": len(rows), "recent": rows[-3:]},
            "budget": budget.snapshot(),
            "model": LLM_MODEL or "未配置（走规则兜底）",
            "permission_mode": AGENT_PERMISSION_MODE,
            "net_policy": TOOL_NET_POLICY,
        }


__all__ = ["Supervisor", "ROUTE_RULES", "DEFAULT_ROLE"]
