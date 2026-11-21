# -*- coding: utf-8 -*-
"""总结智能体：汇总报价记录与运力覆盖，不持有工具。"""

from logistics_agent.Agents.base import ReActAgentBase
from logistics_agent.tools.catalog import quote_rows
from logistics_agent.tools.knowledge import carriers_data, countries, hs_items, ports_data


class SummarizerAgent(ReActAgentBase):
    role = "summarizer"
    system_prompt = "你是总结智能体，只根据已有报价记录与知识库做汇总与复盘，不调用工具、不编造数字。"

    def classic_summarizer_reply(self, text: str, sid: str):
        rows = quote_rows()
        by_country: dict[str, int] = {}
        by_carrier: dict[str, int] = {}
        total = 0.0
        for row in rows:
            by_country[row.get("country", "")] = by_country.get(row.get("country", ""), 0) + 1
            by_carrier[row.get("carrier", "")] = by_carrier.get(row.get("carrier", ""), 0) + 1
            try:
                total += float(row.get("landed_total") or 0)
            except ValueError:
                continue

        lines = [
            "【汇总】",
            f"- 知识库：{len(countries())} 个目的国、{len(hs_items())} 个品类、"
            f"{len(carriers_data())} 家承运商、{len(ports_data())} 个口岸",
            f"- 报价记录：{len(rows)} 条，累计总成本 {total:,.0f} 元",
        ]
        if by_country:
            top = sorted(by_country.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append("- 目的国分布：" + "、".join(f"{k}×{v}" for k, v in top))
        if by_carrier:
            top = sorted(by_carrier.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append("- 承运商分布：" + "、".join(f"{k}×{v}" for k, v in top))

        if not rows:
            lines.append("结论：还没有导出过报价单，建议先跑一次比价并导出，形成可复盘的记录。")
        else:
            lines.append("结论：已形成「询价 → 比价 → 导出报价」闭环，可继续沉淀常走线路的价卡。")
        lines.append("说明：统计只覆盖已导出的报价单，不包含仅比价未导出的询价。")
        return "\n".join(lines), []


__all__ = ["SummarizerAgent"]
