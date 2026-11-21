# -*- coding: utf-8 -*-
"""方案智能体：比价 + 清关 + 税费 + 单证 + 时间线，输出一条龙方案。"""

from logistics_agent.Agents.base import ReActAgentBase
from logistics_agent.core.perception.parser import parse_request


def _money(value) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


class PlanAgent(ReActAgentBase):
    role = "plan"
    system_prompt = (
        "你是方案智能体，负责给出端到端运输方案：推荐承运商、总成本、清关时效、单证清单与时间线。"
        "先调用比价与清关工具，再基于结果做取舍说明；数据不足时列出缺失项，不编造数字。"
    )

    def classic_plan_reply(self, text: str, sid: str):
        trace: list[dict] = []
        req = parse_request(text)
        if not req["country"]:
            return (
                "给我目的国 + 重量 + 货值 + 品类，我就出完整方案（含承运商、税费、清关时效、单证与时间线）。\n"
                "示例：\"300kg 家具，尺寸 120x80x60cm，货值 30000 元，发德国，要海运拼柜\"",
                trace,
            )

        hs = req["hs"]
        hs_name = ""
        if not hs:
            ok, found = self.use("knowledge.lookup_hs", {"keyword": text}, sid, trace)
            if ok and found.get("found"):
                hs, hs_name = found["candidates"][0]["hs"], found["candidates"][0]["name"]

        weight = req["weight_kg"] or 1.0
        ok, plan = self.use(
            "shipment.plan_shipment",
            {
                "country": req["country"],
                "weight_kg": weight,
                "length": req["dims_cm"][0],
                "width": req["dims_cm"][1],
                "height": req["dims_cm"][2],
                "declared_value": req["declared_value"],
                "hs": hs,
                "mode": req["mode"],
                "pieces": req["pieces"],
            },
            sid,
            trace,
        )
        if not ok:
            return f"方案生成失败：{plan.get('error')}", trace
        if not plan.get("ok"):
            lines = [f"暂时无法生成方案：{plan.get('message')}", "", "被排除的承运商："]
            for item in plan.get("excluded", [])[:6]:
                lines.append(f"- {item['carrier']}：{item['reason']}")
            return "\n".join(lines), trace

        chosen = plan["chosen_quote"]
        rec = plan["recommendation"]
        cl = plan["clearance"]
        lines = [
            f"【全链路方案】{plan['query']['country_name']} · {weight:g}kg"
            + (f" · {hs_name}（HS {hs}）" if hs else "")
            + f"｜货值 {_money(req['declared_value'])} 元",
            "",
            f"推荐：{rec['carrier']}（{rec['mode']}）",
            f"总成本：{_money(chosen['landed_total'])} 元（运费 {_money(chosen['total'])} + 税费 {_money(chosen['tax'])}）",
            f"时效：{chosen['eta_days'][0]:g}-{chosen['eta_days'][1]:g} 天（预计 {chosen['eta_date']} 签收），"
            f"其中清关 {cl['clearance_days'][0]:g}-{cl['clearance_days'][1]:g} 天（置信度 {cl['confidence']}）",
            f"计费：{chosen['charge_basis']}",
            "",
            "备选（按评分）：",
        ]
        for alt in plan["alternatives"][:3]:
            lines.append(
                f"- {alt['carrier']}（{alt['mode']}）：{_money(alt['landed_total'])} 元｜"
                f"{alt['eta_date']}｜评分 {alt['score']}"
            )

        lines.extend(["", f"税费：{plan['duty_summary']}"])
        if plan["tax"]:
            lines.append(
                f"计税口径：关税率 {plan['tax']['duty_rate'] * 100:.1f}%，"
                f"{plan['tax']['vat_name']} {plan['tax']['vat_rate'] * 100:.1f}%，"
                f"免税额 {plan['tax']['duty_de_minimis']:,.0f} 元"
            )

        lines.append("")
        lines.append("单证：")
        lines.append(f"- 需备：{'、'.join(plan['docs']['required'])}")
        if plan["docs"]["missing"]:
            lines.append(f"- 缺失：{'、'.join(plan['docs']['missing'])}")
        else:
            lines.append("- 缺失：无（按已备材料判断）")

        lines.extend(["", "时间线："])
        for item in plan["timeline"]:
            lines.append(f"- {item['stage']}：第 {item['start']}-{item['end']} 天（约 {item['date']} 前完成）")

        if plan["risks"]:
            lines.extend(["", "风险："])
            for risk in plan["risks"][:6]:
                lines.append(f"- {risk}")

        if plan["advice"]:
            lines.extend(["", "优化建议："])
            for adv in plan["advice"][:5]:
                lines.append(f"- {adv}")

        lines.extend(["", "口径：" + "；".join(plan["assumptions"][:3])])
        return "\n".join(lines), trace


__all__ = ["PlanAgent"]
