# -*- coding: utf-8 -*-
"""方案工具：全链路运输方案编排、报价单导出、报价统计。"""

import hashlib
import time
from datetime import date, timedelta

from config import PATHS
from logistics_agent.core.toolkit.decorator import register
from logistics_agent.tools.catalog import QUOTE_FIELDS, append_rows, quote_rows
from logistics_agent.tools.clearance import estimate, normalize_mode
from logistics_agent.tools.knowledge import carriers_data, find_hs_entry, resolve_country
from logistics_agent.tools.rates import compare
from logistics_agent.tools.knowledge import compute_duty, required_docs


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _timeline(transit: list[int], clearance: list[float], tail: float, mode: str) -> list[dict]:
    """把总时效拆成可执行的里程碑：备货 → 干线 → 清关 → 派送（按真实天数推算）。"""
    today = date.today()
    prep = 1 if mode in ("express", "air") else 3
    t_lo, t_hi = int(transit[0]), int(transit[1])
    c_lo, c_hi = float(clearance[0]), float(clearance[1])

    points = {
        "prep": prep,
        "transit_end": prep + t_hi,
        "clear_end": prep + t_hi + c_hi,
        "total": prep + t_hi + c_hi + tail,
    }
    mil = [
        {
            "stage": "备货与订舱",
            "start": 0,
            "end": points["prep"],
            "note": "整柜/拼柜建议提前 10-14 天订舱" if mode in ("fcl", "lcl") else "快递/空运舱位提前 2-3 天确认",
        },
        {
            "stage": "出运与干线",
            "start": points["prep"],
            "end": points["transit_end"],
            "note": f"干线 {t_lo}-{t_hi} 天，以实际航班/船期为准",
        },
        {
            "stage": "目的国清关",
            "start": points["transit_end"],
            "end": round(points["clear_end"], 1),
            "note": f"清关 {c_lo:g}-{c_hi:g} 个工作日，单证齐备可缩短",
        },
        {
            "stage": "尾程派送",
            "start": round(points["clear_end"], 1),
            "end": round(points["total"], 1),
            "note": f"派送约 {tail:g} 天，偏远地区另加 1-2 天",
        },
    ]
    for item in mil:
        item["date"] = (today + timedelta(days=int(round(item["end"])))).strftime("%m-%d")
    return mil


@register(roles=["plan"], name="shipment.plan_shipment")
def plan_shipment(
    country: str,
    weight_kg: float = 1,
    length: float = 0,
    width: float = 0,
    height: float = 0,
    declared_value: float = 0,
    hs: str = "",
    mode: str = "",
    pieces: int = 1,
    docs_provided=None,
    prefer: str = "balance",
) -> dict:
    """生成全链路方案：比价 + 清关时效 + 税费 + 单证 + 时间线 + 风险与优化建议。"""
    if isinstance(docs_provided, str):
        docs_provided = [x.strip() for x in docs_provided.replace("，", ",").split(",") if x.strip()]

    base_docs_assumed = False
    if not docs_provided:
        # 未声明单证时按「已备基础单证」估算，与比价/清关口径保持一致
        docs_provided = ["商业发票", "装箱单"]
        base_docs_assumed = True

    country_entry = resolve_country(country)
    if not country_entry:
        raise ValueError(f"未识别的目的国：{country or '（空）'}")

    cmp_result = compare(
        country, weight_kg, length, width, height, mode, pieces,
        hs, declared_value, docs_provided, prefer,
    )
    if not cmp_result.get("quotes"):
        return {
            "ok": False,
            "message": cmp_result.get("message", "无可选承运商"),
            "excluded": cmp_result.get("excluded", []),
        }

    pick = cmp_result["recommendation"]
    chosen = next((q for q in cmp_result["quotes"] if q["carrier"] == pick["carrier"]), cmp_result["quotes"][0])
    hs_entry = find_hs_entry(hs=hs) if hs else None
    cl = estimate(
        country=country, mode=chosen["mode"], hs=hs,
        docs_provided=docs_provided, carrier=chosen["carrier"],
        declared_value=declared_value,
    )
    docs = required_docs(hs_entry, country_entry)
    covered = cl["docs_covered"]
    missing = cl["missing_docs"]

    advice: list[str] = []
    if missing:
        advice.append("优先补齐单证：" + "、".join(missing[:4]) + "，可压缩 1-3 天清关时间")
    if hs_entry and hs_entry.get("risk") == "high":
        advice.append("高查验品类，建议提前取得认证并保留检测报告；清关行优先选自营清关承运商")
    if country_entry.get("vat_rate") and declared_value:
        advice.append(
            f"目的国{country_entry.get('vat_name', 'VAT')}税率 {country_entry['vat_rate'] * 100:.0f}%，"
            "报价时按含税口径向客户披露"
        )
    if chosen["mode"] in ("fcl", "lcl"):
        advice.append("海运转关环节多，建议预留 3-5 天缓冲，并购买一切险")
    if hs_entry and hs_entry.get("dg"):
        advice.append("危险品需承运商书面确认，包装与 UN38.3/MSDS 必须齐备")
    if declared_value and declared_value <= country_entry.get("duty_de_minimis_cny", 0):
        advice.append("低于免税额，可享关税豁免；但不得为避税拆单或低报申报价值")

    weaknesses = list(cl["risks"])
    for flag in chosen.get("flags", []):
        if flag not in weaknesses:
            weaknesses.append(flag)

    from logistics_agent.core.live_feed import safe_panel

    live = safe_panel(
        "plan",
        country_name=country_entry["name"],
        mode_name=chosen.get("mode_name", ""),
        hs=hs,
    )

    return {
        "ok": True,
        "query": cmp_result["query"],
        "live": live,
        "recommendation": pick,
        "chosen_quote": chosen,
        "alternatives": [
            {"carrier": q["carrier"], "mode": q["mode_name"], "landed_total": q["landed_total"],
             "eta_date": q["eta_date"], "score": q["score"]}
            for q in cmp_result["quotes"][:5]
        ],
        "tax": cmp_result.get("tax"),
        "duty_summary": (
            f"关税 {cmp_result['tax']['duty']} 元 + {cmp_result['tax']['vat_name']} {cmp_result['tax']['vat']} 元 "
            f"= {cmp_result['tax']['total_tax']} 元"
            if cmp_result.get("tax")
            else "未提供货值，税费未计"
        ),
        "clearance": cl,
        "docs": {"required": docs, "covered": covered, "missing": missing},
        "timeline": _timeline(
            chosen["transit_days"], cl["clearance_days"], cl["tail_days"], chosen["mode"]
        ),
        "risks": weaknesses,
        "advice": advice,
        "assumptions": (
            (["未提供单证清单，按已备商业发票与装箱单估算，其余按缺失计"] if base_docs_assumed else [])
            + cmp_result["assumptions"]
            + cl["assumptions"]
        ),
    }


@register(roles=["quote", "plan"], name="shipment.export_quote")
def export_quote(
    country: str,
    carrier: str,
    mode: str = "",
    weight_kg: float = 1,
    declared_value: float = 0,
    freight_total: float = 0,
    tax: float = 0,
    landed_total: float = 0,
    eta_date: str = "",
    note: str = "",
    confirm: bool = False,
) -> dict:
    """导出一张报价单到 data/quotes/quote_log.csv（写操作，plan 模式需 confirm=true）。"""
    country_entry = resolve_country(country)
    if not country_entry:
        raise ValueError(f"未识别的目的国：{country}")
    if not carrier:
        raise ValueError("carrier 不能为空")

    quote_id = hashlib.md5(f"{_now()}|{carrier}|{country}|{weight_kg}".encode("utf-8")).hexdigest()[:10]
    row = {
        "quote_id": quote_id,
        "created_at": _now(),
        "country": country_entry["code"],
        "mode": normalize_mode(mode),
        "carrier": carrier,
        "weight_kg": round(float(weight_kg or 0), 2),
        "declared_value": round(float(declared_value or 0), 2),
        "freight_total": round(float(freight_total or 0), 2),
        "tax": round(float(tax or 0), 2),
        "landed_total": round(float(landed_total or 0), 2),
        "eta_date": eta_date,
        "note": str(note)[:200],
    }
    append_rows(PATHS["quote_log"], [row], QUOTE_FIELDS)
    return {
        "ok": True,
        "quote_id": quote_id,
        "row": row,
        "file": str(PATHS["quote_log"].relative_to(PATHS["data"].parent)),
        "total_quotes": len(quote_rows()),
    }


@register(roles=["quote", "plan"], name="shipment.quote_stats")
def quote_stats() -> dict:
    """统计已导出报价：总量、按国家/承运商分布、金额合计与最近 5 条。"""
    rows = quote_rows()
    by_country: dict[str, int] = {}
    by_carrier: dict[str, int] = {}
    total_amount = 0.0
    for row in rows:
        by_country[row.get("country", "")] = by_country.get(row.get("country", ""), 0) + 1
        by_carrier[row.get("carrier", "")] = by_carrier.get(row.get("carrier", ""), 0) + 1
        try:
            total_amount += float(row.get("landed_total") or 0)
        except ValueError:
            continue
    return {
        "total": len(rows),
        "by_country": by_country,
        "by_carrier": by_carrier,
        "amount_sum": round(total_amount, 2),
        "recent": rows[-5:],
        "file": "data/quotes/quote_log.csv",
    }


__all__ = ["plan_shipment", "export_quote", "quote_stats"]
