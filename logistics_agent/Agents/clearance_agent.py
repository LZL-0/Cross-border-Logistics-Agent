# -*- coding: utf-8 -*-
"""清关智能体：目的国清关时效预估、影响因素与口岸拥堵。"""

from logistics_agent.Agents.base import ReActAgentBase
from logistics_agent.core.perception.parser import parse_request

_DOC_WORDS = {
    "商业发票": ["发票", "invoice"],
    "装箱单": ["装箱单", "packing"],
    "空运/海运提单": ["提单", "运单", "bl", "awb"],
    "EORI 号": ["eori"],
    "EORI(GB) 号": ["eori"],
    "CE 符合性声明": ["ce"],
    "CE": ["ce"],
    "FDA 备案": ["fda"],
    "FCC": ["fcc"],
}


def _detect_docs(text: str) -> list[str]:
    low = text.lower()
    found = []
    for doc, words in _DOC_WORDS.items():
        if any(w in low for w in words):
            found.append(doc)
    return found


class ClearanceAgent(ReActAgentBase):
    role = "clearance"
    system_prompt = (
        "你是清关智能体，负责估算目的国清关时效并解释影响因素。"
        "必须给出区间与置信度，说明单证/认证/口岸/旺季的影响，不承诺单点时效。"
    )

    def classic_clearance_reply(self, text: str, sid: str):
        trace: list[dict] = []
        req = parse_request(text)
        if not req["country"]:
            port_ok, ports = self.use("clearance.port_status", {}, sid, trace)
            lines = [
                "请补充目的国（美国/德国/英国/日本/新加坡/澳洲等）与运输方式，我再估算清关时效。",
                "",
                "当前拥堵最高的口岸（可作为起运/目的口岸参考）：",
            ]
            if port_ok:
                for item in ports.get("top_congested", []):
                    lines.append(
                        f"- {item['name']}（{item['code']}）：拥堵指数 {item['congestion_days']} 天，"
                        f"平均滞港 {item['avg_dwell']} 天"
                    )
            return "\n".join(lines), trace

        hs = req["hs"]
        hs_name = ""
        if text and not hs:
            ok, found = self.use("knowledge.lookup_hs", {"keyword": text}, sid, trace)
            if ok and found.get("found"):
                hs, hs_name = found["candidates"][0]["hs"], found["candidates"][0]["name"]

        docs = _detect_docs(text)
        ok, est = self.use(
            "clearance.estimate_clearance",
            {
                "country": req["country"],
                "mode": req["mode"] or "express",
                "hs": hs,
                "docs_provided": docs,
                "declared_value": req["declared_value"],
            },
            sid,
            trace,
        )
        if not ok:
            return f"清关时效估算失败：{est.get('error')}", trace

        lo, hi = est["clearance_days"]
        lines = [
            f"【清关时效】{est['country_name']} · {est['mode_name']}"
            + (f" · {hs_name}（HS {hs}）" if hs else ""),
            f"预估清关 {lo:g}-{hi:g} 个工作日（置信度：{est['confidence']}），尾程派送另计约 {est['tail_days']:g} 天",
            "",
            "影响因素分解：",
        ]
        for item in est["breakdown"]:
            sign = "+" if item["days"] > 0 else ""
            lines.append(f"- {item['item']}：{sign}{item['days']:g} 天")

        if est["missing_docs"]:
            lines.append("")
            lines.append("缺失单证：" + "、".join(est["missing_docs"]))
        if est["docs_covered"]:
            lines.append("已备单证：" + "、".join(est["docs_covered"]))

        if est["risks"]:
            lines.append("")
            lines.append("风险提示：")
            for risk in est["risks"][:6]:
                lines.append(f"- {risk}")

        lines.append("")
        lines.append("假设：" + "；".join(est["assumptions"]))
        lines.append(f"注：{est['note']}")
        return "\n".join(lines), trace


__all__ = ["ClearanceAgent"]
