# -*- coding: utf-8 -*-
"""运价工具：承运商目录、多承运商报价、综合比价与评分。

计费口径（演示基线）：
- 快递：计费重 = max(实重, 体积重)，体积重 = 长×宽×高 / divisor × 件数
- 空运：同上，divisor 6000，按整公斤进位
- 拼柜/铁路：W/M 计费，取体积吨与重量吨较大者
- 整柜：按柜型报价，体积 ≤28CBM 走 20GP，否则 40HQ
"""

import math
import time
from datetime import date, timedelta

from config import AGENCY_CLEARANCE_FEE, PEAK_MONTHS, TAIL_DAYS
from logistics_agent.core.toolkit.decorator import register
from logistics_agent.tools.clearance import estimate, normalize_mode
from logistics_agent.tools.knowledge import (
    carriers_data,
    compute_duty,
    find_hs_entry,
    resolve_country,
)

_MODE_CN = {"express": "国际快递", "air": "空运", "ocean": "海运", "lcl": "海运拼柜", "fcl": "海运整柜", "rail": "铁路"}
_MODE_TAIL = {"express": "express", "air": "air", "ocean": "ocean", "lcl": "ocean", "fcl": "ocean", "rail": "rail"}
_PREP_DAYS = {"express": 1, "air": 1, "ocean": 3, "lcl": 3, "fcl": 3, "rail": 3}


def _round_up(value: float, step: float) -> float:
    if step <= 0:
        return round(value, 3)
    return round(math.ceil(round(value / step, 6)) * step, 3)


def money_cny(value) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _is_peak() -> bool:
    return int(time.strftime("%m")) in PEAK_MONTHS


def _cbm(weight_kg: float, dims: tuple[float, float, float], pieces: int) -> float:
    l, w, h = dims
    if l and w and h:
        return (l * w * h / 1_000_000.0) * max(1, pieces)
    # 未给尺寸时按 300kg/CBM 估算，避免拼柜报价为空
    return max(0.01, weight_kg / 300.0)


def _mode_match(requested: str, carrier_mode: str) -> bool:
    if not requested:
        return True
    req = normalize_mode(requested)
    if req == "ocean":
        return carrier_mode in ("lcl", "fcl")
    if req == "lcl":
        return carrier_mode == "lcl"
    if req == "fcl":
        return carrier_mode == "fcl"
    return carrier_mode == req


def _price_one(carrier: dict, country: str, weight_kg: float, dims, pieces: int) -> dict | None:
    bl = carrier.get("billing", {})
    pr = carrier.get("price", {})
    mode = carrier["modes"][0]
    factor = float(carrier.get("lane_factor", {}).get(country, 1.1))
    peak = _is_peak()
    l, w, h = dims

    if mode in ("express", "air"):
        divisor = float(bl.get("divisor", 5000))
        round_step = float(bl.get("rounding", 0.5))
        vol = (l * w * h / divisor) * max(1, pieces) if (l and w and h) else 0.0
        cw = max(float(weight_kg), vol, float(bl.get("min_kg", 0.5)))
        cw = _round_up(cw, round_step)
        base = float(pr.get("base_fee", 0)) + float(pr.get("per_kg", 0)) * cw * factor
        freight = max(float(pr.get("min_charge", 0)), base)
        fuel = freight * float(pr.get("fuel_pct", 0))
        peak_fee = freight * float(pr.get("peak_pct", 0)) if peak else 0.0
        extras = float(pr.get("clearance_fee", 0)) + float(pr.get("doc_fee", 0))
        total = freight + fuel + peak_fee + extras
        return {
            "chargeable": round(cw, 2),
            "charge_basis": f"计费重 {cw}kg（体积重系数 {int(divisor)}）",
            "cbm": None,
            "freight": round(freight, 2),
            "fuel": round(fuel, 2),
            "peak_surcharge": round(peak_fee, 2),
            "clearance_or_doc": round(extras, 2),
            "dest_fee": 0.0,
            "total": round(total, 2),
        }

    if mode in ("lcl", "rail"):
        cbm = _cbm(weight_kg, dims, pieces)
        wm = max(cbm, float(weight_kg) / 1000.0)
        base = float(pr.get("per_cbm", 0)) * wm * factor
        freight = max(float(pr.get("min_charge", 0)), base)
        fuel = freight * float(pr.get("fuel_pct", 0))
        peak_fee = freight * float(pr.get("peak_pct", 0)) if peak else 0.0
        dest = float(pr.get("dest_fee", 0))
        total = freight + fuel + peak_fee + dest
        return {
            "chargeable": round(wm, 2),
            "charge_basis": f"W/M 计费 {round(wm, 2)}（体积 {round(cbm, 2)}CBM / 重量 {round(weight_kg / 1000, 3)}吨）",
            "cbm": round(cbm, 2),
            "freight": round(freight, 2),
            "fuel": round(fuel, 2),
            "peak_surcharge": round(peak_fee, 2),
            "clearance_or_doc": 0.0,
            "dest_fee": round(dest, 2),
            "total": round(total, 2),
        }

    if mode == "fcl":
        cbm = _cbm(weight_kg, dims, pieces)
        box = "20GP" if cbm <= 28 else "40HQ"
        freight = float(pr.get("container", {}).get(box, 0)) * factor
        fuel = freight * float(pr.get("fuel_pct", 0))
        peak_fee = freight * float(pr.get("peak_pct", 0)) if peak else 0.0
        local = float(pr.get("local_fee", 0))
        total = freight + fuel + peak_fee + local
        return {
            "chargeable": round(cbm, 2),
            "charge_basis": f"{box} 整柜（按体积 {round(cbm, 2)}CBM 选型）",
            "cbm": round(cbm, 2),
            "container": box,
            "freight": round(freight, 2),
            "fuel": round(fuel, 2),
            "peak_surcharge": round(peak_fee, 2),
            "clearance_or_doc": round(local, 2),
            "dest_fee": 0.0,
            "total": round(total, 2),
        }
    return None


def _dims_tuple(length: float, width: float, height: float) -> tuple[float, float, float]:
    return (float(length or 0), float(width or 0), float(height or 0))


def quote_all(
    country: str,
    weight_kg: float = 1,
    length: float = 0,
    width: float = 0,
    height: float = 0,
    mode: str = "",
    pieces: int = 1,
    hs: str = "",
    declared_value: float = 0,
) -> dict:
    country_entry = resolve_country(country)
    if not country_entry:
        raise ValueError(f"未识别的目的国：{country or '（空）'}")

    code = country_entry["code"]
    weight = max(0.1, float(weight_kg or 0))
    dims = _dims_tuple(length, width, height)
    hs_entry = find_hs_entry(hs=hs) if hs else None
    need_dg = bool(hs_entry and hs_entry.get("dg"))

    quotes, excluded = [], []
    for carrier in carriers_data():
        name = carrier["name"]
        if code not in carrier.get("lanes", []):
            excluded.append({"carrier": name, "reason": f"该承运商不覆盖 {country_entry['name']} 线路"})
            continue
        if not _mode_match(mode, carrier["modes"][0]):
            excluded.append({"carrier": name, "reason": "运输方式不匹配"})
            continue
        max_kg = float(carrier.get("billing", {}).get("max_kg", 1e9))
        if weight > max_kg:
            excluded.append({"carrier": name, "reason": f"超出单票重量上限 {max_kg}kg，需分票或改走空运/海运"})
            continue
        max_declared = float(carrier.get("max_declared", 1e12))
        if declared_value and declared_value > max_declared:
            excluded.append({"carrier": name, "reason": f"超出可承运货值上限 {max_declared} 元"})
            continue
        if need_dg and not carrier.get("dangerous"):
            excluded.append({"carrier": name, "reason": "不承运危险品（锂电池/液体等）"})
            continue

        price = _price_one(carrier, code, weight, dims, pieces)
        if not price:
            continue

        transit = carrier.get("transit", {}).get(code)
        if not transit:
            excluded.append({"carrier": name, "reason": "缺少该线路时效基线"})
            continue
        tmin, tmax = int(transit[0]), int(transit[1])

        quotes.append(
            {
                "carrier_id": carrier["id"],
                "carrier": name,
                "service": carrier.get("service", ""),
                "mode": carrier["modes"][0],
                "mode_name": _MODE_CN.get(carrier["modes"][0], ""),
                "own_clearance": bool(carrier.get("own_clearance")),
                "ontime": float(carrier.get("ontime", 0.9)),
                "max_kg": max_kg if max_kg < 1e8 else None,
                "max_declared": max_declared if max_declared < 1e11 else None,
                "transit_days": [tmin, tmax],
                "notes": carrier.get("notes", ""),
                **price,
            }
        )

    return {
        "query": {
            "country": code,
            "country_name": country_entry["name"],
            "mode": normalize_mode(mode) if mode else "不限",
            "weight_kg": round(weight, 2),
            "dims_cm": [dims[0], dims[1], dims[2]],
            "pieces": int(pieces or 1),
            "hs": hs_entry["hs"] if hs_entry else "",
            "declared_value": float(declared_value or 0),
        },
        "currency": "CNY",
        "count": len(quotes),
        "quotes": quotes,
        "excluded": excluded,
        "assumptions": [
            "报价为演示价卡，不含燃油以外的目的港杂费、关税与保险",
            "体积重按承运商 divisor 计算，实际以承运商复测为准",
            "旺季附加费按当前月份自动叠加",
        ],
    }


def _eta_window(transit, clearance_range, tail, mode) -> tuple[list[float], str, str]:
    """下单到签收的总时效：备货 + 干线 + 清关 + 尾程，与方案时间线同一口径。"""
    prep = _PREP_DAYS.get(mode, 1)
    lo = round(prep + transit[0] + clearance_range[0] + tail, 1)
    hi = round(prep + transit[1] + clearance_range[1] + tail, 1)
    today = date.today()
    d_lo = today + timedelta(days=int(math.ceil(lo)))
    d_hi = today + timedelta(days=int(math.ceil(hi)))
    return [lo, hi], d_lo.strftime("%m-%d"), d_hi.strftime("%m-%d")


def compare(
    country: str,
    weight_kg: float = 1,
    length: float = 0,
    width: float = 0,
    height: float = 0,
    mode: str = "",
    pieces: int = 1,
    hs: str = "",
    declared_value: float = 0,
    docs_provided: list[str] | None = None,
    prefer: str = "balance",
) -> dict:
    base = quote_all(country, weight_kg, length, width, height, mode, pieces, hs, declared_value)
    country_entry = resolve_country(country)
    hs_entry = find_hs_entry(hs=hs) if hs else None
    tax = compute_duty(hs_entry, country_entry, declared_value, 0) if declared_value else None

    rows = []
    for quote in base["quotes"]:
        cl = estimate(
            country=country,
            mode=quote["mode"],
            hs=hs,
            docs_provided=docs_provided,
            carrier=quote["carrier_id"],
            declared_value=declared_value,
        )
        tail = TAIL_DAYS.get(_MODE_TAIL[quote["mode"]], 2.0)
        eta, d_lo, d_hi = _eta_window(quote["transit_days"], cl["clearance_days"], tail, quote["mode"])

        tax_amount = tax["total_tax"] if tax else 0.0
        # 不自营清关的承运商按统一代理清关费折算，保证「总成本」可比
        agency_fee = 0.0 if quote["own_clearance"] else float(AGENCY_CLEARANCE_FEE)
        landed = quote["total"] + tax_amount + agency_fee
        risk_penalty = 0.0
        flags = []
        if not quote["own_clearance"]:
            risk_penalty += 0.2
            flags.append(f"含代理清关预估 {money_cny(agency_fee)} 元（自行清关可省）")
        if "机场到机场" in quote["service"] or "港到港" in quote["service"] or "站到站" in quote["service"]:
            risk_penalty += 0.1
            flags.append("不含尾程派送（需另行安排）")
        if hs_entry and hs_entry.get("dg") and quote["mode"] in ("express", "air"):
            risk_penalty += 0.3
        if cl["confidence"] == "低":
            risk_penalty += 0.3

        rows.append(
            {
                **quote,
                "tax": tax_amount,
                "duty": tax["duty"] if tax else 0.0,
                "vat": tax["vat"] if tax else 0.0,
                "agency_fee": round(agency_fee, 2),
                "landed_total": round(landed, 2),
                "clearance_days": cl["clearance_days"],
                "eta_days": eta,
                "eta_date": f"{d_lo} ~ {d_hi}",
                "confidence": cl["confidence"],
                "risk_penalty": round(risk_penalty, 2),
                "flags": flags,
            }
        )

    if not rows:
        return {**base, "quotes": [], "excluded": base["excluded"], "message": "没有满足条件的承运商，请放宽重量/货值或更换运输方式"}

    costs = [r["landed_total"] for r in rows]
    etas = [r["eta_days"][1] for r in rows]
    cost_min, cost_max = min(costs), max(costs)
    eta_min, eta_max = min(etas), max(etas)
    cost_span = max(cost_max - cost_min, 1e-6)
    eta_span = max(eta_max - eta_min, 1e-6)

    for row in rows:
        n_cost = (row["landed_total"] - cost_min) / cost_span
        n_eta = (row["eta_days"][1] - eta_min) / eta_span
        score = 100 - 50 * n_cost - 30 * n_eta - 8 * (1 - row["ontime"]) * 10 - 10 * row["risk_penalty"]
        row["score"] = round(max(0.0, min(100.0, score)), 1)

    cheapest = min(rows, key=lambda r: r["landed_total"])
    fastest = min(rows, key=lambda r: r["eta_days"][1])
    best = max(rows, key=lambda r: r["score"])

    if prefer == "cheapest":
        pick = cheapest
    elif prefer == "fastest":
        pick = fastest
    else:
        pick = best

    gap_cost = round(fastest["landed_total"] - cheapest["landed_total"], 2)
    gap_day = round(cheapest["eta_days"][1] - fastest["eta_days"][1], 1)
    tradeoff = (
        f"最省为 {cheapest['carrier']}（{cheapest['landed_total']} 元，{cheapest['eta_date']} 到）；"
        f"最快为 {fastest['carrier']}（{fastest['eta_date']} 到，贵 {gap_cost} 元）；"
        f"若按时效优先，可少等约 {gap_day} 天。"
    )

    for row in rows:
        row.pop("carrier_id", None)

    rows.sort(key=lambda r: r["score"], reverse=True)
    return {
        **base,
        "quotes": rows,
        "tax": tax,
        "cheapest": cheapest["carrier"],
        "fastest": fastest["carrier"],
        "best_value": best["carrier"],
        "recommendation": {
            "carrier": pick["carrier"],
            "mode": pick["mode_name"],
            "landed_total": pick["landed_total"],
            "eta_date": pick["eta_date"],
            "eta_days": pick["eta_days"],
            "score": pick["score"],
            "reason": f"综合成本、时效与准点率评分最高（{pick['score']} 分）",
            "tradeoff": tradeoff,
        },
    }


@register(roles=["quote", "plan"], name="rates.list_carriers")
def list_carriers(country: str = "", mode: str = "") -> dict:
    """列出承运商能力：覆盖线路、运输方式、时效、准点率、危险品限制。"""
    code = ""
    if country:
        entry = resolve_country(country)
        if not entry:
            raise ValueError(f"未识别的目的国：{country}")
        code = entry["code"]

    items = []
    for carrier in carriers_data():
        if code and code not in carrier.get("lanes", []):
            continue
        if mode and not _mode_match(mode, carrier["modes"][0]):
            continue
        items.append(
            {
                "id": carrier["id"],
                "name": carrier["name"],
                "mode": carrier["modes"][0],
                "mode_name": _MODE_CN.get(carrier["modes"][0], ""),
                "service": carrier.get("service", ""),
                "lanes": carrier.get("lanes", []),
                "ontime": carrier.get("ontime"),
                "own_clearance": carrier.get("own_clearance"),
                "dangerous": carrier.get("dangerous", []),
                "max_declared": carrier.get("max_declared"),
                "notes": carrier.get("notes", ""),
            }
        )
    return {"count": len(items), "carriers": items}


@register(roles=["quote", "plan"], name="rates.quote_rates")
def quote_rates(
    country: str,
    weight_kg: float = 1,
    length: float = 0,
    width: float = 0,
    height: float = 0,
    mode: str = "",
    pieces: int = 1,
    hs: str = "",
    declared_value: float = 0,
) -> dict:
    """按重量/尺寸向所有可用承运商取报价明细（不做排序与评分）。"""
    return quote_all(country, weight_kg, length, width, height, mode, pieces, hs, declared_value)


@register(roles=["quote", "plan"], name="rates.compare_rates")
def compare_rates(
    country: str,
    weight_kg: float = 1,
    length: float = 0,
    width: float = 0,
    height: float = 0,
    mode: str = "",
    pieces: int = 1,
    hs: str = "",
    declared_value: float = 0,
    docs_provided=None,
    prefer: str = "balance",
) -> dict:
    """多承运商比价：含税费、清关时效、评分与推荐（cheapest/fastest/best_value）。"""
    if isinstance(docs_provided, str):
        docs_provided = [x.strip() for x in docs_provided.replace("，", ",").split(",") if x.strip()]
    result = compare(
        country, weight_kg, length, width, height, mode, pieces, hs, declared_value, docs_provided, prefer
    )
    # 板块级实时快照：SCFI 指数 + 汇率 + 运价/燃油资讯（取不到就降级，不影响报价）
    from logistics_agent.core.live_feed import safe_panel

    query = result.get("query", {})
    result["live"] = safe_panel(
        "quote",
        country_name=query.get("country_name", ""),
        mode_name=query.get("mode", ""),
    )
    return result


__all__ = ["quote_all", "compare", "list_carriers", "quote_rates", "compare_rates"]
