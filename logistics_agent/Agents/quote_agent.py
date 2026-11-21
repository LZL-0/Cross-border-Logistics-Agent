# -*- coding: utf-8 -*-
"""比价智能体：多承运商报价、总成本排序与推荐。"""

from logistics_agent.Agents.base import ReActAgentBase
from logistics_agent.core.perception.parser import COUNTRY_NAME, parse_request


def _money(value) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


_IMPLIED_MODE = ("专线", "小包", "拼柜", "整柜", "班列", "空运", "海运")


def _label(carrier: str, mode_name: str) -> str:
    """承运商名称里已含方式时不再重复括号，避免「云途物流（专线）（国际快递）」。"""
    if not mode_name or mode_name in carrier or any(k in carrier for k in _IMPLIED_MODE):
        return carrier
    return f"{carrier}（{mode_name}）"


class QuoteAgent(ReActAgentBase):
    role = "quote"
    system_prompt = (
        "你是比价智能体，负责按货物参数向承运商取报价并做综合比较。"
        "必须给出总成本（运费+关税+增值税）、时效区间与推荐理由；"
        "数据不足时先向用户确认目的国、重量、尺寸、货值与运输方式，不编造价格。"
    )

    def classic_quote_reply(self, text: str, sid: str):
        trace: list[dict] = []
        req = parse_request(text)
        if not req["country"]:
            return (
                "请补充以下信息后我再比价：\n"
                "1) 目的国（美国 / 德国 / 英国 / 日本 / 新加坡 / 澳洲等）\n"
                "2) 重量（如 20kg）与单件尺寸（如 40x30x20cm）\n"
                "3) 货值（如 5000 元）与品类（如 服装 / 手机 / 玩具）\n"
                "4) 期望方式（快递 / 空运 / 海运拼柜 / 整柜 / 铁路）\n"
                "示例：\"20kg 服装，尺寸 50x40x30cm，货值 5000 元，发德国，比一下价\"",
                trace,
            )

        weight = req["weight_kg"] or 1.0
        notes = []
        if not req["weight_kg"]:
            notes.append("未识别到重量，按 1kg 试算")

        hs = req["hs"]
        hs_name = ""
        if not hs:
            ok, found = self.use("knowledge.lookup_hs", {"keyword": text}, sid, trace)
            if ok and found.get("found"):
                item = found["candidates"][0]
                hs, hs_name = item["hs"], item["name"]

        args = {
            "country": req["country"],
            "weight_kg": weight,
            "length": req["dims_cm"][0],
            "width": req["dims_cm"][1],
            "height": req["dims_cm"][2],
            "pieces": req["pieces"],
            "hs": hs,
            "declared_value": req["declared_value"],
            "mode": req["mode"],
        }
        ok, result = self.use("rates.compare_rates", args, sid, trace)
        if not ok:
            return f"比价失败：{result.get('error')}", trace

        quotes = result.get("quotes") or []
        if not quotes:
            return (
                f"{result.get('message', '没有可用承运商')}\n"
                f"被排除的承运商：\n"
                + "\n".join(f"- {x['carrier']}：{x['reason']}" for x in result.get("excluded", [])[:6]),
                trace,
            )

        lines = [
            f"【比价结果】{result['query']['country_name']} · {weight:g}kg · "
            f"{'不限方式' if not result['query']['mode'] or result['query']['mode'] == '不限' else result['query']['mode']}"
            f"｜货值 {_money(req['declared_value'])} 元"
            + (f"｜品类 {hs_name}（HS {hs}）" if hs else ""),
            f"共 {result['count']} 家可选，按综合评分排序（总成本含运费+关税+增值税，不自营清关的已计入代理费）：",
            "",
        ]
        for idx, quote in enumerate(quotes[:8], start=1):
            cost_parts = f"运费 {_money(quote['total'])} + 税费 {_money(quote['tax'])}"
            if quote.get("agency_fee"):
                cost_parts += f" + 代理清关 {_money(quote['agency_fee'])}"
            lines.append(
                f"{idx}. {_label(quote['carrier'], quote['mode_name'])}"
                f"｜总成本 {_money(quote['landed_total'])} 元"
                f"（{cost_parts}）"
                f"｜时效 {quote['eta_days'][0]:g}-{quote['eta_days'][1]:g} 天（{quote['eta_date']}）"
                f"｜准点率 {quote['ontime'] * 100:.0f}%｜评分 {quote['score']}"
            )
            if quote.get("flags"):
                lines.append(f"    注意：{'；'.join(quote['flags'])}")

        rec = result["recommendation"]
        lines.extend(
            [
                "",
                f"推荐：{_label(rec['carrier'], rec.get('mode_name', '') or rec.get('mode', ''))}｜{_money(rec['landed_total'])} 元｜"
                f"{rec['eta_days'][0]:g}-{rec['eta_days'][1]:g} 天（{rec['eta_date']}）",
                f"理由：{rec['reason']}",
                f"权衡：{rec['tradeoff']}",
            ]
        )

        if result.get("excluded"):
            lines.append("")
            lines.append("未纳入比较：")
            for item in result["excluded"][:5]:
                lines.append(f"- {item['carrier']}：{item['reason']}")

        if notes:
            lines.append("")
            lines.append("说明：" + "；".join(notes))
        lines.append("口径：" + "；".join(result.get("assumptions", [])[:3]))
        return "\n".join(lines), trace


__all__ = ["QuoteAgent"]
