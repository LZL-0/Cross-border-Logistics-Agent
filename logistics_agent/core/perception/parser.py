# -*- coding: utf-8 -*-
"""轻量结构化解析：把一句中文需求拆成询价/清关所需参数。

规则兜底模式（未配置 LLM Key）完全依赖这里的解析结果，
因此别名表覆盖面决定了「不接模型也能用」的上限。
"""

import re

# 国家别名（按最长匹配优先）
COUNTRY_ALIASES = {
    "US": ["美国", "美线", "usa", "us", "united states", "america", "洛杉矶", "纽约"],
    "CA": ["加拿大", "canada", "ca", "多伦多", "温哥华"],
    "DE": ["德国", "de", "germany", "汉堡", "法兰克福", "柏林"],
    "FR": ["法国", "fr", "france", "巴黎", "勒阿弗尔"],
    "NL": ["荷兰", "nl", "netherlands", "holland", "鹿特丹", "阿姆斯特丹"],
    "IT": ["意大利", "it", "italy", "米兰", "罗马"],
    "ES": ["西班牙", "es", "spain", "马德里", "巴塞罗那"],
    "GB": ["英国", "gb", "uk", "united kingdom", "伦敦", "费利克斯托"],
    "JP": ["日本", "jp", "japan", "东京", "大阪", "名古屋"],
    "SG": ["新加坡", "sg", "singapore"],
    "AU": ["澳大利亚", "澳洲", "au", "australia", "悉尼", "墨尔本"],
}

# 运输方式别名
MODE_ALIASES = {
    "express": ["快递", "express", "国际快递", "门到门", "小包", "专线"],
    "air": ["空运", "air", "空派", "机场到机场", "空加派"],
    "ocean": ["海运", "ocean", "海派", "船运", "整柜", "拼柜", "fcl", "lcl"],
    "lcl": ["拼柜", "拼箱", "lcl"],
    "fcl": ["整柜", "整箱", "fcl"],
    "rail": ["铁路", "班列", "中欧班列", "rail"],
}

COUNTRY_NAME = {
    "US": "美国", "CA": "加拿大", "DE": "德国", "FR": "法国", "NL": "荷兰",
    "IT": "意大利", "ES": "西班牙", "GB": "英国", "JP": "日本", "SG": "新加坡",
    "AU": "澳大利亚",
}

_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克|KG)")
_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[x×*X]\s*(\d+(?:\.\d+)?)\s*[x×*X]\s*(\d+(?:\.\d+)?)")
_VALUE_RE = re.compile(
    r"(?:货值|价值|申报(?:价值)?|金额|单价|总价)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(万元|元|块|rmb|cny|美元|美金|usd)?",
    re.I,
)
_PIECES_RE = re.compile(r"(\d+)\s*(?:件|箱|个|托盘|pcs|ctn)")
_HS_RE = re.compile(r"(?:hs|海关编码|编码)\s*[:：]?\s*(\d{6,10})", re.I)


def _find_alias(text: str, table: dict[str, list[str]]) -> str:
    low = text.lower()
    best_code, best_len = "", 0
    for code, aliases in table.items():
        for alias in aliases:
            if alias.lower() in low and len(alias) > best_len:
                # 英文短别名（如 us/ca）需词边界，避免误命中
                if alias.isascii() and len(alias) <= 3:
                    if not re.search(rf"(?<![a-z]){re.escape(alias.lower())}(?![a-z])", low):
                        continue
                best_code, best_len = code, len(alias)
    return best_code


def parse_country(text: str) -> str:
    return _find_alias(text, COUNTRY_ALIASES)


def parse_mode(text: str) -> str:
    """返回 express / air / lcl / fcl / rail / ocean（ocean 为未细分）。"""
    for mode in ("lcl", "fcl"):
        if _find_alias(text, {mode: MODE_ALIASES[mode]}):
            return mode
    if _find_alias(text, {"rail": MODE_ALIASES["rail"]}):
        return "rail"
    if _find_alias(text, {"air": MODE_ALIASES["air"]}):
        return "air"
    if _find_alias(text, {"express": MODE_ALIASES["express"]}):
        return "express"
    if _find_alias(text, {"ocean": MODE_ALIASES["ocean"]}):
        return "ocean"
    return ""


def parse_weight(text: str) -> float:
    match = _WEIGHT_RE.search(text or "")
    if not match:
        return 0.0
    return float(match.group(1))


def parse_dims(text: str) -> tuple[float, float, float]:
    match = _DIM_RE.search(text or "")
    if not match:
        return (0.0, 0.0, 0.0)
    return tuple(float(x) for x in match.groups())  # type: ignore[return-value]


def parse_value(text: str) -> float:
    match = _VALUE_RE.search(text or "")
    if not match:
        return 0.0
    value = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "万元":
        value *= 10000
    elif unit in ("美元", "美金", "usd"):
        value *= 7.2  # 演示汇率
    return value


def parse_pieces(text: str) -> int:
    match = _PIECES_RE.search(text or "")
    return int(match.group(1)) if match else 1


def parse_hs(text: str) -> str:
    match = _HS_RE.search(text or "")
    if match:
        return match.group(1)
    # 直接写 6-10 位纯数字也认
    for token in re.findall(r"\b\d{6,10}\b", text or ""):
        return token
    return ""


def parse_request(text: str) -> dict:
    """把一句话解析成结构化请求；未识别字段留空。"""
    country = parse_country(text)
    mode = parse_mode(text)
    return {
        "country": country,
        "country_name": COUNTRY_NAME.get(country, country),
        "mode": mode,
        "weight_kg": parse_weight(text),
        "dims_cm": parse_dims(text),
        "declared_value": parse_value(text),
        "pieces": parse_pieces(text),
        "hs": parse_hs(text),
    }


__all__ = [
    "parse_request",
    "parse_country",
    "parse_mode",
    "parse_weight",
    "parse_dims",
    "parse_value",
    "parse_pieces",
    "parse_hs",
    "COUNTRY_NAME",
]
