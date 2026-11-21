# -*- coding: utf-8 -*-
"""合规智能体：HS 归类、关税与增值税、认证与单证要求。"""

from logistics_agent.Agents.base import ReActAgentBase
from logistics_agent.core.perception.parser import parse_request


class ComplianceAgent(ReActAgentBase):
    role = "compliance"
    system_prompt = (
        "你是合规智能体，负责 HS 归类、关税与进口增值税测算、认证与单证清单。"
        "税率以知识库基线为准并注明免税额；归类不确定时给出候选并提示以海关裁定为准。"
    )

    def classic_compliance_reply(self, text: str, sid: str):
        trace: list[dict] = []
        req = parse_request(text)

        hs = req["hs"]
        ok, found = self.use("knowledge.lookup_hs", {"hs": hs, "keyword": text}, sid, trace)
        if not ok or not found.get("found"):
            lines = ["未能在知识库中匹配到品类，请提供更具体的品名或 6-10 位 HS 编码。", ""]
            lines.append("知识库覆盖的品类：")
            lines.append("手机、笔记本、锂电池、T恤、鞋、玩具、木家具、化妆品、保健品、箱包、塑料件、线缆、五金")
            return "\n".join(lines), trace

        item = found["candidates"][0]
        hs = item["hs"]

        if not req["country"]:
            lines = [
                f"【归类】{item['name']} → HS {hs}",
                f"风险等级：{item['risk']}" + ("（危险品）" if item["dg"] else ""),
                f"认证要求：{'、'.join(item['certs']) or '无'}",
                "",
                "告诉我目的国（如 德国/美国/英国），我再算关税与增值税。",
                "",
                "各国关税率参考：",
            ]
            for code, rate in list(item["duty_by_country"].items())[:8]:
                lines.append(f"- {code}：{rate * 100:.1f}%")
            return "\n".join(lines), trace

        value = req["declared_value"]
        if not value:
            lines_note = "未识别到货值，按 1000 元试算。"
            value = 1000.0
        else:
            lines_note = ""

        ok, tax = self.use(
            "knowledge.duty_calc",
            {"hs": hs, "country": req["country"], "declared_value": value},
            sid,
            trace,
        )
        if not ok:
            return f"税费测算失败：{tax.get('error')}", trace

        lines = [
            f"【归类与税费】{item['name']} → HS {hs}｜目的国 {tax['country_name']}",
            f"申报货值 {tax['declared_value']:,.0f} 元｜关税率 {tax['duty_rate'] * 100:.1f}%"
            + ("（低于免税额，免征）" if tax["duty_exempt"] else ""),
            f"关税 {tax['duty']:,.0f} 元 + {tax['vat_name']}（{tax['vat_rate'] * 100:.1f}%）{tax['vat']:,.0f} 元 "
            f"= 税费合计 {tax['total_tax']:,.0f} 元",
            f"到岸成本（货值+税费）{tax['landed_cost']:,.0f} 元",
            "",
            f"免税额：关税 {tax['duty_de_minimis']:,.0f} 元以下免征；"
            f"{tax['vat_name']} 起征点按目的国规则执行",
            f"认证：{'、'.join(item['certs']) or '无'}",
            f"单证：{'、'.join(tax['docs'])}",
            f"风险等级：{item['risk']}" + ("｜危险品，需承运商确认" if item["dg"] else ""),
        ]
        if item.get("notes"):
            lines.append(f"提示：{item['notes']}")
        if lines_note:
            lines.append(lines_note)
        lines.append("注：税率为演示基线，正式申报以目的国海关核定为准，不构成报关依据。")
        return "\n".join(lines), trace


__all__ = ["ComplianceAgent"]
