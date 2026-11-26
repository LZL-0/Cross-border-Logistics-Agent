# -*- coding: utf-8 -*-
"""国内集货工具：集货 / 头程货代名录检索 + 官网公开信息读取。

数据来源：data/knowledge/consolidators.json（公开信息整理）。
合规约束：只读取名录内登记的官网域名，不抓平台站内数据、不绕验证码。
"""

import json
import re
import time
from functools import lru_cache
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from config import PATHS
from logistics_agent.core.toolkit.decorator import register
from logistics_agent.tools.knowledge import resolve_country

_TIMEOUT = 8.0
_MAX_CHARS = 3000
_UA = "Mozilla/5.0 (compatible; LogisticsEngine/1.0; public-page-reader)"


@lru_cache(maxsize=1)
def _companies() -> list[dict]:
    """读取集货商名录（只读，进程内缓存）。"""
    path = PATHS["knowledge"] / "consolidators.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("companies", [])


def _item(company: dict) -> dict:
    """统一输出结构，避免把内部字段直接抛给模型。"""
    return {
        "id": company.get("id", ""),
        "name": company.get("name", ""),
        "city": company.get("city", ""),
        "type": company.get("type", ""),
        "services": company.get("services", []),
        "lanes": company.get("lanes", []),
        "pickup_cities": company.get("pickup_cities", []),
        "website": company.get("website", ""),
        "notes": company.get("notes", ""),
    }


@register(roles=["quote", "plan"], name="consolidator.find_consolidator")
def find_consolidator(keyword: str = "", city: str = "", service: str = "", lane: str = "") -> dict:
    """检索国内集货与头程货代：按关键词、城市、服务类型、目的国筛选，返回公司名与官网。"""
    lane_code = ""
    if lane:
        entry = resolve_country(lane)
        lane_code = entry["code"].upper() if entry else lane.strip().upper()

    items = []
    for company in _companies():
        blob = json.dumps(company, ensure_ascii=False).lower()
        if keyword and keyword.lower() not in blob:
            continue
        if city and city not in " ".join([company.get("city", "")] + company.get("pickup_cities", [])):
            continue
        if service and service not in "".join(company.get("services", [])) + company.get("type", ""):
            continue
        if lane_code and lane_code not in [str(x).upper() for x in company.get("lanes", [])]:
            continue
        items.append(_item(company))
    return {"count": len(items), "items": items}


def _find_by_name(name: str) -> dict | None:
    """按公司名、简称或 id 匹配名录条目。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    for company in _companies():
        if key == str(company.get("id", "")).lower() or key in company.get("name", "").lower():
            return company
    return None


def _allowed_host(url: str) -> bool:
    """只允许名录内登记域名的页面（含其子域），防止任意网址抓取。"""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    for company in _companies():
        site = (urlparse(company.get("website", "")).hostname or "").lower()
        if site and (host == site or host.endswith("." + site)):
            return True
    return False


@register(roles=["quote", "plan"], name="consolidator.site_profile")
def site_profile(name: str = "", url: str = "", max_chars: int = 800) -> dict:
    """读取名录内集货商官网公开页面，返回标题、简介与正文摘要（仅限已登记域名）。"""
    company = _find_by_name(name) if name else None
    target = (url or (company or {}).get("website", "")).strip()
    if not target:
        raise ValueError("请提供名录内的公司名或官网网址，可先用 consolidator.find_consolidator 查询")
    if not _allowed_host(target):
        raise ValueError("只允许读取名录内已登记的官网域名，请先检索名录")

    limit = max(200, min(int(max_chars or 800), _MAX_CHARS))
    fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
    base = {"name": (company or {}).get("name", ""), "url": target, "fetched_at": fetched_at}
    try:
        resp = httpx.get(
            target,
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": _UA},
        )
    except Exception as exc:  # noqa: BLE001  网络不可达也要给模型可读的错误
        return {**base, "ok": False, "error": f"请求失败：{exc}"}

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = (meta.get("content") or "").strip() if meta else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return {
        **base,
        "ok": resp.status_code < 400,
        "status": resp.status_code,
        "title": title,
        "description": description,
        "excerpt": text[:limit],
        "truncated": len(text) > limit,
        "note": "官网页面为公开信息，运价与时效以官网当期公告为准",
    }


__all__ = ["find_consolidator", "site_profile"]
