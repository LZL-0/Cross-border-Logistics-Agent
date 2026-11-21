# -*- coding: utf-8 -*-
"""清关时效工具：按目的国基线 + 单证 + 品类风险 + 口岸拥堵 + 旺季叠加估算。

输出永远是「区间 + 置信度 + 影响因素分解」，不做单点承诺。
"""

import time

from config import PEAK_MONTHS, TAIL_DAYS
from logistics_agent.core.toolkit.decorator import register
from logistics_agent.tools.knowledge import (
    carriers_data,
    find_hs_entry,
    ports_data,
    required_docs,
    resolve_country,
)

_MODE_TO_DAYS = {"express": "express", "air": "air", "ocean": "ocean", "lcl": "ocean", "fcl": "ocean", "rail": "rail"}
_MODE_TO_TAIL = {"express": "express", "air": "air", "ocean": "ocean", "lcl": "ocean", "fcl": "ocean", "rail": "rail"}
_SPREAD = {"express": 0.8, "air": 1.5, "ocean": 2.5, "rail": 2.0}
_MODE_CN = {"express": "国际快递", "air": "空运", "ocean": "海运", "lcl": "海运拼柜", "fcl": "海运整柜", "rail": "铁路"}


def normalize_mode(mode: str) -> str:
    text = (mode or "").strip().lower()
    if text in _MODE_TO_DAYS:
        return text
    zh = {
        "快递": "express", "国际快递": "express", "专线": "express", "小包": "express",
        "空运": "air", "空派": "air",
        "海运": "ocean", "海派": "ocean", "整柜": "fcl", "拼柜": "lcl",
        "铁路": "rail", "班列": "rail", "中欧班列": "rail",
    }
    return zh.get(text, "express")


def _docs_covered(required: list[str], provided: list[str]) -> tuple[list[str], list[str]]:
    """返回 (已覆盖, 缺失)；按关键词包含关系做宽松匹配。"""
    if not provided:
        return [], list(required)
    covered, missing = [], []
    for doc in required:
        hit = any((p and (p in doc or doc in p)) for p in provided)
        if hit:
            covered.append(doc)
        else:
            missing.append(doc)
    return covered, missing


def _port_congestion(port: str, country_code: str, mode: str) -> tuple[float, str]:
    ports = ports_data()
    if port:
        for item in ports:
            if port.upper() == item["code"] or port in item["name"]:
                return float(item["congestion_days"]), item["name"]
        return 0.0, ""
    kind = "airport" if mode == "air" else "seaport"
    matched = [p for p in ports if p["country"] == country_code and p["type"] == kind]
    if not matched:
        return 0.0, ""
    avg = sum(float(p["congestion_days"]) for p in matched) / len(matched)
    return avg, "、".join(p["name"] for p in matched)


def estimate(
    country: str,
    mode: str = "express",
    hs: str = "",
    docs_provided: list[str] | None = None,
    carrier: str = "",
    port: str = "",
    declared_value: float = 0,
) -> dict:
    country_entry = resolve_country(country)
    if not country_entry:
        raise ValueError(f"未识别的目的国：{country or '（空）'}")

    mode = normalize_mode(mode)
    days_key = _MODE_TO_DAYS[mode]
    base = float(country_entry.get("clearance_days", {}).get(days_key, 2.5))

    breakdown = [{"item": "目的国基础清关", "days": round(base, 2)}]
    risks: list[str] = []
    assumptions: list[str] = []

    # 单证缺失
    hs_entry = find_hs_entry(hs=hs) if hs else None
    required = required_docs(hs_entry, country_entry)
    covered, missing = _docs_covered(required, list(docs_provided or []))
    doc_penalty = 0.0
    if missing:
        doc_penalty = round(0.35 * min(4, len(missing)), 2)
        breakdown.append({"item": f"单证缺失（{len(missing)} 项）", "days": doc_penalty})
        risks.append("缺少单证：" + "、".join(missing[:5]))

    # 认证缺失（高风险品类）
    cert_gap = 0.0
    if hs_entry:
        certs = hs_entry.get("certs", [])
        cert_missing = [c for c in certs if not any((p and (p in c or c in p)) for p in (docs_provided or []))]
        if cert_missing and hs_entry.get("risk") == "high":
            cert_gap = 2.0
            breakdown.append({"item": "高风险品类认证缺失", "days": cert_gap})
            risks.append("认证未齐：" + "、".join(cert_missing[:3]) + "，可能被扣留或退运")

    # 品类风险
    risk_penalty = 0.0
    if hs_entry:
        risk = hs_entry.get("risk", "medium")
        risk_penalty = {"high": 1.2, "medium": 0.5, "low": 0.0}.get(risk, 0.5)
        if risk_penalty:
            breakdown.append({"item": f"品类风险（{risk}）", "days": risk_penalty})
        if hs_entry.get("dg"):
            risk_penalty += 1.0
            breakdown.append({"item": "危险品（锂电池/液体等）", "days": 1.0})
            risks.append("危险品需承运商书面确认舱位与包装（UN38.3/MSDS）")

    # 承运商自营清关
    clearance_gain = 0.0
    if carrier:
        for item in carriers_data():
            if carrier == item["id"] or carrier in item["name"]:
                if item.get("own_clearance"):
                    clearance_gain = -0.6
                    breakdown.append({"item": f"{item['name']} 自营清关", "days": -0.6})
                break

    # 口岸拥堵
    congestion, port_name = _port_congestion(port, country_entry["code"], mode)
    congestion_penalty = round(congestion * 0.6, 2)
    if congestion_penalty:
        breakdown.append({"item": f"口岸拥堵（{port_name}）", "days": congestion_penalty})
        risks.append(f"{port_name} 当前拥堵指数偏高（+{congestion_penalty} 天）")

    # 旺季
    peak_penalty = 0.0
    month = int(time.strftime("%m"))
    if month in PEAK_MONTHS and month in country_entry.get("peak_months", []):
        peak_penalty = 1.2 if mode in ("ocean", "air", "rail") else 0.6
        breakdown.append({"item": "旺季（舱位/查验排队）", "days": peak_penalty})
        risks.append("当前处于旺季，舱位与查验排队时间上升")

    # 低货值简化
    exempt_gain = 0.0
    if declared_value and declared_value <= float(country_entry.get("duty_de_minimis_cny", 0)):
        exempt_gain = -0.3
        breakdown.append({"item": "低于免税额（简化申报）", "days": -0.3})

    raw = base + doc_penalty + cert_gap + risk_penalty + clearance_gain + congestion_penalty + peak_penalty + exempt_gain
    est = max(0.5, round(raw, 1))
    spread = _SPREAD.get(mode, 1.5)
    if cert_gap or (missing and len(missing) >= 3):
        spread += 1.0

    tail = TAIL_DAYS.get(_MODE_TO_TAIL[mode], 2.0)
    assumptions.append("清关时效为工作日口径，不含航班/船期等待")
    assumptions.append("未提供单证清单时按『已备齐常规单证』计算，实际以提交材料为准")

    confidence = "高" if not missing and not cert_gap else ("中" if len(missing) <= 2 else "低")

    return {
        "country": country_entry["code"],
        "country_name": country_entry["name"],
        "mode": mode,
        "mode_name": _MODE_CN.get(mode, mode),
        "clearance_days": [est, round(est + spread, 1)],
        "confidence": confidence,
        "breakdown": [b for b in breakdown if b["days"]],
        "missing_docs": missing,
        "docs_covered": covered,
        "required_docs": required,
        "risks": risks,
        "assumptions": assumptions,
        "tail_days": tail,
        "note": "时效为估算区间，受船期、查验率与旺季影响，不作为赔付承诺",
    }


@register(roles=["clearance", "plan"], name="clearance.estimate_clearance")
def estimate_clearance(
    country: str,
    mode: str = "express",
    hs: str = "",
    docs_provided=None,
    carrier: str = "",
    port: str = "",
    declared_value: float = 0,
) -> dict:
    """估算目的国清关时效区间，并给出影响因素分解与风险清单。"""
    if isinstance(docs_provided, str):
        docs_provided = [x.strip() for x in docs_provided.replace("，", ",").split(",") if x.strip()]
    return estimate(
        country=country,
        mode=mode,
        hs=hs,
        docs_provided=docs_provided,
        carrier=carrier,
        port=port,
        declared_value=declared_value,
    )


@register(roles=["clearance", "plan"], name="clearance.port_status")
def port_status(port: str = "") -> dict:
    """查询起运/目的口岸拥堵指数与平均滞港时间；不传参时返回拥堵 Top5。"""
    ports = ports_data()
    if port:
        for item in ports:
            if port.upper() == item["code"] or port in item["name"]:
                return {
                    "found": True,
                    "port": item,
                    "advice": "拥堵指数已计入清关时效预估；建议预留缓冲或改走邻近口岸",
                }
        raise ValueError(f"未收录的口岸：{port}")
    top = sorted(ports, key=lambda p: float(p["congestion_days"]), reverse=True)[:5]
    return {"found": True, "top_congested": top, "total": len(ports)}


__all__ = ["estimate", "estimate_clearance", "port_status", "normalize_mode"]
