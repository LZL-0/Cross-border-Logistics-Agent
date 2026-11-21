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
    return _load("hs_codes").get("items", [])


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
    """按商品名或 HS 编码查询归类、目的国税率、认证要求与风险等级。"""
    candidates = match_hs_candidates(keyword, hs, limit)
    if not candidates:
        return {
            "found": False,
            "query": {"keyword": keyword, "hs": hs},
            "candidates": [],
            "hint": "知识库未收录该品类，请提供 6-10 位 HS 编码或更具体的品名（如 手机/服装/玩具/锂电池）",
        }
    brief = []
    for item in candidates:
        brief.append(
            {
                "hs": item["hs"],
                "name": item["name"],
                "duty_by_country": item.get("duty", {}),
                "certs": item.get("certs", []),
                "risk": item.get("risk", "medium"),
                "dg": item.get("dg", False),
                "notes": item.get("notes", ""),
            }
        )
    return {"found": True, "count": len(brief), "candidates": brief}


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
