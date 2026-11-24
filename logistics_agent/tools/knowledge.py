# -*- coding: utf-8 -*-
"""知识库工具：HS 归类、税率计算、目的国进口规则。

数据来源：data/knowledge/*.json（演示基线），全部只读、不联网。
"""

import json
from functools import lru_cache

from config import PATHS
from logistics_agent.core.toolkit.decorator import register


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    path = PATHS[name]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def countries() -> list[dict]:
    return _load("countries").get("countries", [])


def hs_items() -> list[dict]:
    """内置基线 + 已人工确认的自动收录条目（auto_items，停用的不算）。"""
    doc = _load("hs_codes")
    base = doc.get("items", [])
    auto = [x for x in doc.get("auto_items", []) if not x.get("disabled")]
    return list(base) + list(auto)


def carriers_data() -> list[dict]:
    return _load("carriers").get("carriers", [])


def ports_data() -> list[dict]:
    return _load("ports").get("ports", [])


def resolve_country(value: str) -> dict | None:
    """把 US / us / 美国 / 美线 等写法统一解析成国家条目。"""
    text = (value or "").strip()
    if not text:
        return None
    upper = text.upper()
    for item in countries():
        if item["code"].upper() == upper:
            return item
    for item in countries():
        if item["name"] in text or text in item["name"]:
            return item
    # 常见别名兜底
    alias = {
        "美线": "US", "美国站": "US", "欧洲": "DE", "欧线": "DE",
        "英国站": "GB", "日本站": "JP", "澳洲": "AU",
    }
    for key, code in alias.items():
        if key in text:
            return resolve_country(code)
    return None


def find_hs_entry(keyword: str = "", hs: str = "") -> dict | None:
    """按 HS 编码（前缀匹配）或关键词匹配品类条目。"""
    code = (hs or "").strip().replace(" ", "")
    if code:
        for item in hs_items():
            if item["hs"].startswith(code) or code.startswith(item["hs"]):
                return item
    low = (keyword or "").strip().lower()
    if not low:
        return None
    for item in hs_items():
        if low in item["name"].lower():
            return item
        for kw in item.get("keywords", []):
            if kw.lower() in low or low in kw.lower():
                return item
    return None


def match_hs_candidates(keyword: str = "", hs: str = "", limit: int = 5) -> list[dict]:
    code = (hs or "").strip().replace(" ", "")
    low = (keyword or "").strip().lower()
    hits: list[dict] = []
    for item in hs_items():
        if code and (item["hs"].startswith(code) or code.startswith(item["hs"])):
            hits.append(item)
            continue
        if low and (low in item["name"].lower() or any(kw.lower() in low for kw in item.get("keywords", []))):
            hits.append(item)
    if not hits and low:
        # 拆词再试一次：识别「发一批棉T恤」这类长句
        for item in hs_items():
            if any(kw in low for kw in item.get("keywords", []) if len(kw) >= 2):
                hits.append(item)
    return hits[: max(1, int(limit))]


def compute_duty(hs_entry: dict | None, country: dict, declared_value: float, freight: float = 0.0) -> dict:
    """按演示基线计算关税与进口增值税（CIF 口径简化）。"""
    declared = max(0.0, float(declared_value))
    rate = 0.05
    if hs_entry:
        rate = float(hs_entry.get("duty", {}).get(country["code"], 0.05))

    duty = 0.0
    duty_exempt = declared <= float(country.get("duty_de_minimis_cny", 0))
    if not duty_exempt:
        duty = declared * rate

    vat_base = declared + duty + max(0.0, float(freight))
    vat_rate = float(country.get("vat_rate", 0))
    vat_exempt = (declared + max(0.0, float(freight))) <= float(country.get("vat_de_minimis_cny", 0))
    vat = 0.0 if vat_exempt else vat_base * vat_rate

    return {
        "country": country["code"],
        "country_name": country["name"],
        "hs": hs_entry["hs"] if hs_entry else "",
        "declared_value": round(declared, 2),
        "duty_rate": round(rate, 4),
        "duty": round(duty, 2),
        "duty_exempt": duty_exempt,
        "duty_de_minimis": country.get("duty_de_minimis_cny", 0),
        "vat_rate": round(vat_rate, 4),
        "vat": round(vat, 2),
        "vat_name": country.get("vat_name", "VAT"),
        "total_tax": round(duty + vat, 2),
        "landed_cost": round(declared + duty + vat, 2),
        "note": "关税/增值税为演示基线，正式申报以目的国海关核定为准",
    }


def required_docs(hs_entry: dict | None, country: dict) -> list[str]:
    docs = list(country.get("docs", []))
    if hs_entry:
        for cert in hs_entry.get("certs", []):
            if cert not in docs:
                docs.append(cert)
    return docs


@register(roles=["compliance", "quote", "plan"], name="knowledge.lookup_hs")
def lookup_hs(keyword: str = "", hs: str = "", limit: int = 5) -> dict:
    """按商品名或 HS 编码查询归类、目的国税率、认证要求与风险等级。

    未收录时：先查待审候选，再联网检索一次并存入候选表；候选项只做参考，
    未经人工确认不参与 HS 匹配与税费测算。
    """
    candidates = match_hs_candidates(keyword, hs, limit)
    if not candidates:
        payload = {
            "found": False,
            "query": {"keyword": keyword, "hs": hs},
            "candidates": [],
            "hint": "知识库未收录该品类，请提供 6-10 位 HS 编码或更具体的品名（如 手机/服装/玩具/锂电池）",
        }
        if keyword.strip():
            auto_lookup, pending = _auto_lookup(keyword)
            if auto_lookup is not None:
                payload["auto_lookup"] = auto_lookup
            if pending:
                payload["pending_candidates"] = pending
                payload["hint"] = (
                    "知识库未收录该品类，已收录为待审候选（低置信）。"
                    "候选需在「配置 → 品类候选」确认后才参与 HS 匹配与税费测算；"
                    "如需立刻测算，请提供 6-10 位 HS 编码或目的国税率。"
                )
        return payload
    brief = []
    for item in candidates:
        entry = {
            "hs": item["hs"],
            "name": item["name"],
            "duty_by_country": item.get("duty", {}),
            "certs": item.get("certs", []),
            "risk": item.get("risk", "medium"),
            "dg": item.get("dg", False),
            "notes": item.get("notes", ""),
            "source": item.get("source", "baseline"),
        }
        if item.get("source") == "auto":
            entry["confidence"] = item.get("confidence", "low")
            entry["evidence"] = (item.get("evidence") or [])[:2]
        brief.append(entry)
    return {"found": True, "count": len(brief), "candidates": brief}


def _auto_lookup(keyword: str) -> tuple[dict | None, list[dict]]:
    """联网收录（失败/关闭时返回空），并回读当前待审候选。"""
    try:
        from logistics_agent.core import kb_ingest

        result = kb_ingest.ingest(keyword, trigger="lookup_hs")
        pending = kb_ingest.find_pending(keyword)
        slim = {
            "status": "ok" if result.get("ok") else "skipped",
            "skipped": result.get("skipped"),
            "cached": bool(result.get("cached")),
            "errors": (result.get("errors") or [])[:2],
        }
        return slim, [kb_ingest.candidate_brief(c) for c in pending[:2]]
    except Exception as exc:  # noqa: BLE001 联网失败不能把查询链路带崩
        return {"status": "error", "errors": [f"{type(exc).__name__}: {exc}"[:120]]}, []


@register(roles=["compliance", "quote", "plan"], name="knowledge.duty_calc")
def duty_calc(hs: str = "", country: str = "", declared_value: float = 0, freight: float = 0) -> dict:
    """计算指定 HS 与目的国的关税、进口增值税与到岸成本。"""
    country_entry = resolve_country(country)
    if not country_entry:
        raise ValueError(f"未识别的目的国：{country or '（空）'}")
    hs_entry = find_hs_entry(hs=hs)
    if not hs_entry and hs:
        raise ValueError(f"未收录的 HS 编码：{hs}")
    result = compute_duty(hs_entry, country_entry, declared_value, freight)
    result["docs"] = required_docs(hs_entry, country_entry)
    if hs_entry and hs_entry.get("source") == "auto":
        result["source"] = "auto"
        result["confidence"] = hs_entry.get("confidence", "low")
        result["evidence"] = (hs_entry.get("evidence") or [])[:2]
        if not hs_entry.get("duty"):
            result["note"] = "该 HS 来自自动收录（已人工确认），目的国税率未核实，按默认 5% 计；正式申报以海关核定为准"
    # 关税/认证政策实时动态（取不到就降级，不影响测算）
    from logistics_agent.core.live_feed import safe_panel

    result["live"] = safe_panel(
        "compliance",
        country_name=result.get("country_name", ""),
        hs=hs,
    )
    return result


@register(roles=["clearance", "compliance", "plan"], name="knowledge.lookup_country")
def lookup_country(country: str = "") -> dict:
    """查询目的国的进口规则：增值税、免税额、单证、旺季与清关基线。"""
    entry = resolve_country(country)
    if not entry:
        raise ValueError(f"未识别的目的国：{country or '（空）'}")
    return {
        "code": entry["code"],
        "name": entry["name"],
        "region": entry.get("region", ""),
        "vat_rate": entry.get("vat_rate"),
        "vat_name": entry.get("vat_name"),
        "duty_de_minimis_cny": entry.get("duty_de_minimis_cny"),
        "vat_de_minimis_cny": entry.get("vat_de_minimis_cny"),
        "clearance_days": entry.get("clearance_days", {}),
        "docs": entry.get("docs", []),
        "peak_months": entry.get("peak_months", []),
        "notes": entry.get("notes", ""),
    }


__all__ = [
    "countries",
    "hs_items",
    "carriers_data",
    "ports_data",
    "resolve_country",
    "find_hs_entry",
    "match_hs_candidates",
    "compute_duty",
    "required_docs",
    "lookup_hs",
    "duty_calc",
    "lookup_country",
]
