# -*- coding: utf-8 -*-
"""实时面板块：把散落的实时工具聚合成「板块级快照」，供比价/清关/合规/方案与看板复用。

分工：
- 单个实时工具（search / fetch / scfi / fx）在 `tools/realtime.py`；
- 这里只负责「按板块挑话题、并发取数、去重、缓存、失败降级」，
  保证任何一个实时源挂掉都不会把主业务（报价/清关/税费）拖崩。

返回结构统一为：
    {
      "enabled": bool,
      "ok": bool,
      "as_of": "YYYY-MM-DD HH:MM:SS",
      "market": {"scfi": {...}|null, "fx": {...}|null, "errors": [...]},
      "news": [{"topic","title","url","snippet","published","fetched_at"}],
      "errors": [...],
      "disclaimer": "..."
    }
"""

import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

from config import LIVE_NEWS_LIMIT, LIVE_TTL, PATHS, REALTIME_ENABLED, REALTIME_TIMEOUT

_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
_LAST_FILE = PATHS["telemetry"] / "market_cache.json"
# 周更落盘的行情基线（kb_sync 写入），作为二级兜底：实时失败 + 无缓存时仍能给出上周口径
_BASELINE_FILE = PATHS["knowledge"] / "market_baseline.json"

_DISCLAIMER = "实时数据仅作行情参考；运价以承运商协议价、税费以目的国海关核定为准。"

# 检索常把百科词条顶到前面（"德国"→百度百科），对物流资讯没有价值，直接丢掉
_JUNK_HOSTS = (
    "baike.baidu.com",
    "zh.wikipedia.org",
    "en.wikipedia.org",
    "wiki.mbalib.com",
    "baike.sogou.com",
    "mfa.gov.cn",
)
_JUNK_TITLE_MARKS = ("_百度百科", "百度百科", "维基百科", "MBA智库百科")


def _is_junk(item: dict) -> bool:
    url = (item.get("url") or "").lower()
    if any(host in url for host in _JUNK_HOSTS):
        return True
    title = item.get("title") or ""
    return any(mark in title for mark in _JUNK_TITLE_MARKS)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _cache_get(key: str) -> dict | None:
    with _LOCK:
        item = _CACHE.get(key)
    if not item:
        return None
    stamped, payload = item
    if time.time() - stamped > LIVE_TTL:
        with _LOCK:
            _CACHE.pop(key, None)
        return None
    return {**payload, "cached": True}


def _cache_put(key: str, payload: dict) -> dict:
    with _LOCK:
        _CACHE[key] = (time.time(), payload)
    return payload


# ------------------------- 行情 -------------------------


def _read_last() -> dict | None:
    try:
        if _LAST_FILE.exists():
            loaded = json.loads(_LAST_FILE.read_text(encoding="utf-8"))
            if loaded.get("scfi") or loaded.get("fx"):
                return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return _read_baseline()


def _read_baseline() -> dict | None:
    """把周更写入的 market_baseline.json 转成 market() 同构结构。"""
    try:
        doc = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    scfi = doc.get("scfi") or {}
    fx = doc.get("fx") or {}
    if not scfi and not fx:
        return None
    payload = {"as_of": doc.get("_meta", {}).get("updated_at") or doc.get("as_of"), "scfi": None, "fx": None}
    if scfi.get("value"):
        payload["scfi"] = {
            "current": {"period": scfi.get("period"), "value": scfi.get("value")},
            "previous": {"period": "", "value": scfi.get("previous")},
            "change": scfi.get("change"),
            "change_pct": scfi.get("change_pct"),
            "trend": scfi.get("trend"),
            "source": scfi.get("source"),
            "baseline": True,
        }
    if fx.get("rates"):
        payload["fx"] = {
            "base": fx.get("base") or "USD",
            "rates": fx.get("rates") or {},
            "updated_utc": fx.get("updated_utc"),
            "source": fx.get("source"),
            "baseline": True,
        }
    return payload


def _write_last(payload: dict) -> None:
    try:
        _LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_FILE.write_text(
            json.dumps(
                {"as_of": payload.get("as_of"), "scfi": payload.get("scfi"), "fx": payload.get("fx")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def market(force: bool = False) -> dict:
    """SCFI 综合运价指数 + 主要货币兑人民币汇率；两路并发，任一路挂掉用上次成功值兜底。"""
    if not REALTIME_ENABLED:
        return {
            "enabled": False,
            "as_of": _now(),
            "scfi": None,
            "fx": None,
            "errors": ["实时取数已关闭（REALTIME_ENABLED=0）"],
        }

    if not force:
        cached = _cache_get("market")
        if cached:
            return cached

    from logistics_agent.tools.realtime import fx_rate, scfi_index

    errors: list[str] = []
    scfi = fx = None

    pool = ThreadPoolExecutor(max_workers=2)
    jobs = {
        pool.submit(scfi_index): "scfi",
        pool.submit(fx_rate, "USD", "CNY,EUR,JPY,GBP,HKD"): "fx",
    }
    try:
        for fut in as_completed(jobs, timeout=min(REALTIME_TIMEOUT, 8)):
            kind = jobs[fut]
            try:
                value = fut.result()
                if kind == "scfi":
                    scfi = value
                else:
                    fx = value
            except Exception as exc:  # noqa: BLE001  单个源失败只降级
                errors.append(f"{kind}: {type(exc).__name__}: {exc}"[:160])
    except TimeoutError:
        errors.append("行情源响应慢（>8s），本次改用上次成功值")
    finally:
        for fut in jobs:
            fut.cancel()
        pool.shutdown(wait=False)

    # 有一路失败时，用磁盘上的上次成功值兜底，并标记 stale
    stale = False
    if scfi is None or fx is None:
        last = _read_last()
        if last:
            if scfi is None and last.get("scfi"):
                scfi = {**last["scfi"], "stale": True, "as_of": last.get("as_of")}
                stale = True
            if fx is None and last.get("fx"):
                fx = {**last["fx"], "stale": True, "as_of": last.get("as_of")}
                stale = True

    payload = {
        "enabled": True,
        "as_of": _now(),
        "scfi": scfi,
        "fx": fx,
        "stale": stale,
        "errors": errors,
    }
    if scfi and fx and not scfi.get("stale") and not fx.get("stale"):
        _write_last(payload)
    return _cache_put("market", payload)


# ------------------------- 资讯 -------------------------

# 检索引擎（360/Bing）在被限流或识别为爬虫时会退化成「只匹配第一个词」，返回百科词条、
# 新闻站首页、美女榜这类噪声。所以板块资讯走「RSS 订阅（稳定、带时间）+ 关键词检索」双通道，
# 并对检索结果做物流语义过滤——宁可少几条，也不展示跑题内容。
_RSS_FEEDS = ("https://www.ennews.com/rss",)
_RSS_CACHE_KEY = "rss::feeds"


def _xml_tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    text = m.group(1)
    text = re.sub(r"<![CDATA[|]]>", "", text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _pub_label(raw: str) -> str:
    """RFC822 时间转成本地 MM-DD HH:MM，取不到就原样截断。"""
    if not raw:
        return ""
    try:
        stamp = parsedate_to_datetime(raw)
        return time.strftime("%m-%d %H:%M", time.localtime(stamp.timestamp()))
    except (TypeError, ValueError, OverflowError):
        return raw[:16]


def _rss_items() -> list[dict]:
    """抓取物流/跨境媒体的 RSS 订阅，返回带发布时间的资讯条目（进程内缓存 LIVE_TTL）。"""
    cached = _cache_get(_RSS_CACHE_KEY)
    if cached:
        return cached.get("items") or []

    from logistics_agent.tools.realtime import _http_get

    items: list[dict] = []
    for feed in _RSS_FEEDS:
        try:
            status, _, text = _http_get(feed, timeout=min(REALTIME_TIMEOUT, 10))
        except Exception:  # noqa: BLE001 单个订阅源失败不影响整体
            continue
        if status >= 400:
            continue
        for block in re.findall(r"<item>(.*?)</item>", text, re.S):
            title = _xml_tag(block, "title")
            link = _xml_tag(block, "link")
            if not title or not link:
                continue
            snippet = html.unescape(_xml_tag(block, "description"))[:180]
            items.append(
                {
                    "title": html.unescape(title),
                    "url": html.unescape(link),
                    "snippet": snippet,
                    "published": _pub_label(_xml_tag(block, "pubDate")),
                    "topic": "RSS 订阅",
                    "fetched_at": _now(),
                }
            )
    items = items[:40]
    _cache_put(_RSS_CACHE_KEY, {"items": items, "as_of": _now()})
    return items
_RELEVANT_WORDS = (
    "运价", "运费", "运力", "航线", "港口", "码头", "海关", "清关", "关税", "附加费",
    "拥堵", "船期", "集装箱", "船舶", "物流", "货代", "指数", "进口", "出口", "申报",
    "认证", "税率", "海运", "空运", "快递", "航运", "班轮", "舱位",
)
# 标题里必须出现行业词，否则「午盘播报」「交易大百科」这类泛内容会顺着摘要里的“关税”混进来
_CORE_WORDS = tuple(w for w in _RELEVANT_WORDS if w not in ("物流", "指数", "进口", "出口", "申报"))
# 英文检索结果没有中文行业词，用英文词表放行（否则会被整批当噪声丢掉）
_CORE_WORDS_EN = (
    "tariff", "customs", "freight", "shipping", "port", "cargo", "logistics",
    "clearance", "duty", "container", "carrier", "surcharge", "rate", "import", "export", "trade",
)


def _is_junk(item: dict) -> bool:
    url = (item.get("url") or "").lower()
    if any(host in url for host in _JUNK_HOSTS):
        return True
    # 纯域名首页（如 world.huanqiu.com/）不是资讯，直接丢
    path = url.split("://", 1)[-1].split("?", 1)[0].rstrip("/")
    if "/" not in path:
        return True
    title = item.get("title") or ""
    if any(mark in title for mark in _JUNK_TITLE_MARKS):
        return True
    if not any(word in title.lower() for word in _CORE_WORDS) and not any(
        word in title.lower() for word in _CORE_WORDS_EN
    ):
        return True
    text = f"{title} {item.get('snippet') or ''}"
    return not any(word in text for word in _RELEVANT_WORDS)


def _topic_queries(kind: str, country_name: str, mode_name: str, hs: str, port_name: str) -> list[str]:
    cn = country_name or "目的国"
    queries: list[str] = []
    if kind in ("quote", "plan"):
        # 「美线/欧线」这类缩写在 360 上会被拆成单字召回百科，改用「美国航线 运价」稳定得多
        queries.append(f"{cn}航线 运价" if country_name else f"中国到{cn} 运价 最新")
        queries.append("国际快递 燃油附加费")
    if kind in ("clearance", "plan"):
        queries.append(f"{cn} 海关 清关 政策 最新")
        # 带具体口岸时更准；只有国家名时用「国家 + 港口 拥堵」，加「船期」反而会拉到百科词条
        queries.append(f"{port_name} 拥堵 船期 最新" if port_name else f"{cn} 港口 拥堵 最新")
    if kind in ("compliance", "plan"):
        queries.append(f"{cn} 关税 进口 政策 最新")
        queries.append(f"HS {hs} 进口 认证 要求 最新" if hs else f"{cn} 进口 认证 要求 最新")
        # HS 编码在资讯库里几乎不出现，补一条通用认证口径兜底
        if hs:
            queries.append("进口 认证 要求 最新")
    return queries


def _dedupe(items: list[dict]) -> list[dict]:
    """按 URL + 标题前 40 字双键去重（同一篇常被多个渠道转载）。"""
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        if _is_junk(item):
            continue
        url = item.get("url", "")
        title_key = (item.get("title") or "")[:40]
        key = url or title_key
        if key in seen or (title_key and title_key in seen):
            continue
        seen.add(key)
        if title_key:
            seen.add(title_key)
        unique.append(item)
    return unique


def news(queries: list[str], limit: int = 0, country_name: str = "") -> tuple[list[dict], list[str]]:
    """双通道取资讯：关键词检索 + RSS 订阅；命中国家名的 RSS 条目排前面。"""
    limit = max(1, min(int(limit or LIVE_NEWS_LIMIT), 6))
    errors: list[str] = []
    collected: list[dict] = []

    if queries:
        from logistics_agent.tools.realtime import search_web

        def one(query: str):
            result = search_web(query, limit)
            return query, result.get("items", []), result.get("fetched_at", _now())

        # 资讯整体给 8s 预算：检索引擎抽风时不再等它，先拿 RSS 与已回的结果顶上
        budget = min(REALTIME_TIMEOUT, 8)
        pool = ThreadPoolExecutor(max_workers=min(4, len(queries)))
        futures = [pool.submit(one, q) for q in queries]
        try:
            for fut in as_completed(futures, timeout=budget):
                try:
                    query, items, fetched_at = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}"[:140])
                    continue
                for item in items:
                    collected.append({**item, "topic": query, "fetched_at": fetched_at})
        except TimeoutError:
            errors.append(f"检索超时（>{budget:.0f}s），本板块只展示已取回结果与 RSS")
        finally:
            for fut in futures:
                fut.cancel()
            pool.shutdown(wait=False)

    try:
        rss = _rss_items()
    except Exception as exc:  # noqa: BLE001
        rss = []
        errors.append(f"rss: {type(exc).__name__}: {exc}"[:140])

    search_items = _dedupe(collected)

    # RSS 条目自带发布时间，先按「是否提到目的国」分组，国家相关的优先展示
    if country_name:
        related = [i for i in rss if country_name in f"{i.get('title', '')} {i.get('snippet', '')}"]
        others = [i for i in rss if i not in related]
    else:
        related, others = [], rss

    merged = _dedupe(related) + search_items + _dedupe(others)
    return merged, errors


# ------------------------- 板块入口 -------------------------


def panel(kind: str, country_name: str = "", mode_name: str = "", hs: str = "", port_name: str = "") -> dict:
    """取一个板块的实时快照：行情 + 资讯。"""
    key = f"panel::{kind}::{country_name}::{mode_name}::{hs}::{port_name}"
    cached = _cache_get(key)
    if cached:
        return cached

    if not REALTIME_ENABLED:
        return {
            "enabled": False,
            "ok": False,
            "kind": kind,
            "as_of": _now(),
            "market": None,
            "news": [],
            "errors": ["实时取数已关闭（REALTIME_ENABLED=0）"],
            "disclaimer": _DISCLAIMER,
        }

    queries = _topic_queries(kind, country_name, mode_name, hs, port_name)

    # 行情与资讯并发，避免一个慢源把整块拖住
    with ThreadPoolExecutor(max_workers=2) as pool:
        market_future = pool.submit(market)
        news_future = pool.submit(news, queries, 0, country_name)
        try:
            market_payload = market_future.result()
        except Exception as exc:  # noqa: BLE001
            market_payload = {"scfi": None, "fx": None, "as_of": _now(), "errors": [str(exc)[:160]]}
        try:
            items, errors = news_future.result()
        except Exception as exc:  # noqa: BLE001
            items, errors = [], [str(exc)[:160]]

    payload = {
        "enabled": True,
        "ok": bool(market_payload.get("scfi") or market_payload.get("fx") or items),
        "kind": kind,
        "as_of": _now(),
        "market": {
            "scfi": market_payload.get("scfi"),
            "fx": market_payload.get("fx"),
            "as_of": market_payload.get("as_of"),
            "stale": bool(market_payload.get("stale")),
            "errors": market_payload.get("errors") or [],
        },
        "news": items[: LIVE_NEWS_LIMIT * 2],
        "errors": list(dict.fromkeys(list(market_payload.get("errors") or []) + errors)),
        "disclaimer": _DISCLAIMER,
    }
    return _cache_put(key, payload)


def safe_panel(kind: str, **kwargs) -> dict:
    """主业务侧的调用入口：任何异常都吞成结构化降级结果，绝不冒泡。"""
    try:
        return panel(kind, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": REALTIME_ENABLED,
            "ok": False,
            "kind": kind,
            "as_of": _now(),
            "market": None,
            "news": [],
            "errors": [f"{type(exc).__name__}: {exc}"[:160]],
            "disclaimer": _DISCLAIMER,
        }


__all__ = ["market", "news", "panel", "safe_panel"]
