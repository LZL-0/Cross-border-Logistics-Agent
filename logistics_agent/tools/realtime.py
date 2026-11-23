# -*- coding: utf-8 -*-
"""实时信息工具：联网检索 / 网页正文抽取 / 实时汇率。

设计取舍：
1. 不依赖任何第三方 Key：
   - 检索走 360 搜索（`www.so.com/s`）的结果页，Bing 作兜底；
     结果页里的 `data-mdurl` 就是真实目标地址，比跳转链接更适合落库与溯源；
   - 汇率走 `open.er-api.com`（日更，自带更新时刻）。
2. 只读 + 限流 + 限长：结果条数、正文长度、超时、缓存全部来自 `config.py`，
   避免一次问答把上下文和 token 撑爆。
3. 防 SSRF：只允许 http/https，域名解析出的 IP 不得是内网/回环/链路本地/保留地址，
   重定向逐跳复检；响应体硬上限 2MB。
4. 每个返回值都带 `fetched_at` 与 `source`，提示词要求模型必须把取数时间写进回答，
   这样才能区分「实时数据」与「知识库基线」。

注意：搜索引擎结果页属于「尽力而为」的数据源，结构可能随版本变化；
解析失败会落到下一家引擎，全部失败则抛错，让智能体退回知识库基线。
"""

import html
import ipaddress
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from config import (
    FETCH_MAX_CHARS,
    FX_BASE_URL,
    REALTIME_CACHE_TTL,
    REALTIME_ENABLED,
    REALTIME_MAX_RESULTS,
    REALTIME_TIMEOUT,
    SCFI_URL,
    SEARCH_BASE_URL,
    SEARCH_ENGINE,
    SEARCH_FALLBACK_URL,
    SEARCH_NEWS_URL,
    TOOL_NET_POLICY,
)
from logistics_agent.core.toolkit.decorator import register

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_BYTES = 2 * 1024 * 1024
_CACHE: dict[str, tuple[float, dict]] = {}

_BLOCK_TAG_RE = re.compile(r"<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DATE_CN_RE = re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)")

# 360 搜索
_SO_BLOCK_RE = re.compile(r'(?s)<li class="res-list[^"]*"[^>]*>(.*?)</li>')
_SO_H3_RE = re.compile(r'(?s)<h3[^>]*class="res-title[^"]*"[^>]*>(.*?)</h3>')
_SO_ANCHOR_RE = re.compile(r'(?s)<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
_SO_MDURL_RE = re.compile(r'data-mdurl="([^"]+)"')
_SO_SUMMARY_RE = re.compile(r'(?s)<span[^>]*data-result-summary[^>]*>(.*?)</span>')
_SO_DESC_RE = re.compile(r'(?s)<p[^>]*class="res-desc"[^>]*>(.*?)</p>')
# 360 资讯（news.so.com）：li.res-list 的 data-url + h3 + p.summary + span.time
_SO_NEWS_BLOCK_RE = re.compile(
    r'(?s)(<li[^>]*class="[^"]*res-list[^"]*"[^>]*>)(.*?)(?=<li[^>]*class="[^"]*res-list|</ul>)'
)
_SO_NEWS_URL_RE = re.compile(r'data-url="([^"]+)"')
_SO_NEWS_SUMMARY_RE = re.compile(r'(?s)<p[^>]*class="summary[^"]*"[^>]*>(.*?)</p>')
_SO_NEWS_TIME_RE = re.compile(r'(?s)<span[^>]*class="[^"]*time[^"]*"[^>]*>(.*?)</span>')

# Bing 兜底
_BING_ITEM_RE = re.compile(r"(?s)<item>(.*?)</item>")
_BING_ALGO_RE = re.compile(r'(?s)<li class="b_algo".*?</li>')
_BING_HREF_RE = re.compile(r'(?s)<h2[^>]*>.*?<a[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>')


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ------------------------- 缓存与守卫 -------------------------


def _cache_get(key: str) -> dict | None:
    item = _CACHE.get(key)
    if not item:
        return None
    stamped, payload = item
    if time.time() - stamped > REALTIME_CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return {**payload, "cached": True}


def _cache_put(key: str, payload: dict) -> dict:
    _CACHE[key] = (time.time(), payload)
    return payload


def _assert_public(url: str) -> str:
    """只放行公网 http/https，挡住内网、回环、云元数据地址等 SSRF 面。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https：{url}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"缺少域名：{url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"域名解析失败：{host}（{exc}）") from exc
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"拒绝访问非公网地址：{host} → {ip}")
    return url


class _SafeRedirect(HTTPRedirectHandler):
    """重定向逐跳复检，防止 302 跳到内网。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_get(url: str, timeout: float | None = None) -> tuple[int, str, str]:
    """返回 (状态码, 字符集, 正文文本)。"""
    if not REALTIME_ENABLED:
        raise RuntimeError("实时取数已关闭（REALTIME_ENABLED=0）")
    if TOOL_NET_POLICY == "block":
        raise RuntimeError("网络策略为 block，禁止外网请求")
    _assert_public(url)

    req = Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    opener = build_opener(_SafeRedirect())
    try:
        with opener.open(req, timeout=float(timeout or REALTIME_TIMEOUT)) as resp:
            raw = resp.read(_MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            status = resp.status
    except Exception as exc:  # noqa: BLE001  网络异常统一转成可读错误
        detail = f"{type(exc).__name__}: {exc}"
        if "CERTIFICATE_VERIFY_FAILED" in detail:
            hint = "（目标站点证书链异常，常见于个别政府站点，建议改用检索摘要）"
        elif "403" in detail:
            hint = "（目标站点拒绝非浏览器抓取，建议改用检索摘要或换来源）"
        elif "404" in detail:
            hint = "（链接已失效）"
        else:
            hint = ""
        raise RuntimeError(f"请求失败：{urlparse(url).netloc}（{detail}）{hint}") from exc

    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return status, charset, text


# ------------------------- 解析 -------------------------


def _clean(fragment: str, limit: int = 240) -> str:
    """去标签 + 还原实体 + 压空白；行内标签直接删，不加空格。"""
    text = _TAG_RE.sub("", fragment or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse_so(page: str, limit: int) -> list[dict]:
    items: list[dict] = []
    for block in _SO_BLOCK_RE.findall(page):
        if "ec-tuiguang" in block[:200] or "guanggao" in block[:200]:
            continue  # 广告位
        h3 = _SO_H3_RE.search(block)
        if not h3:
            continue
        anchor = _SO_ANCHOR_RE.search(h3.group(1))
        if not anchor:
            continue
        mdurl = _SO_MDURL_RE.search(h3.group(1))
        href = mdurl.group(1) if mdurl else anchor.group(1)
        if not href.startswith("http"):
            continue
        if urlparse(href).netloc.endswith("so.com"):
            continue  # 无法解出真实地址的跳转链直接丢弃
        snippet_match = _SO_SUMMARY_RE.search(block) or _SO_DESC_RE.search(block)
        snippet = _clean(snippet_match.group(1)) if snippet_match else ""
        date = _DATE_CN_RE.search(snippet)
        items.append(
            {
                "title": _clean(anchor.group(2), 120),
                "url": href,
                "snippet": snippet,
                "published": date.group(1) if date else "",
            }
        )
        if len(items) >= limit:
            break
    return items


def _parse_so_news(page: str, limit: int) -> list[dict]:
    """360 资讯搜索（news.so.com）：li.res-list 自带 data-url、摘要和时间，比网页结果页干净。"""
    items: list[dict] = []
    for open_tag, block in _SO_NEWS_BLOCK_RE.findall(page):
        url_m = _SO_NEWS_URL_RE.search(open_tag)
        href = url_m.group(1).strip() if url_m else ""
        if not href.startswith("http"):
            inner = re.search(r'href="(http[^"]+)"', block)
            href = inner.group(1) if inner else ""
        if not href.startswith("http"):
            continue
        title_m = re.search(r"(?s)<h3[^>]*>(.*?)</h3>", block)
        summary_m = _SO_NEWS_SUMMARY_RE.search(block)
        time_m = _SO_NEWS_TIME_RE.search(block)
        title = _clean(title_m.group(1), 120) if title_m else ""
        if not title:
            attr = re.search(r'title="([^"]{6,})"', block)
            title = _clean(attr.group(1), 120) if attr else ""
        if not title:
            continue
        items.append(
            {
                "title": title,
                "url": html.unescape(href),
                "snippet": _clean(summary_m.group(1)) if summary_m else "",
                "published": _clean(time_m.group(1), 20) if time_m else "",
            }
        )
        if len(items) >= limit:
            break
    return items


def _parse_bing(page: str, limit: int) -> list[dict]:
    """优先按 RSS 结构解析，拿不到再退回结果页 HTML。"""
    items: list[dict] = []
    for block in _BING_ITEM_RE.findall(page):
        title = re.search(r"(?s)<title>(.*?)</title>", block)
        link = re.search(r"(?s)<link>(.*?)</link>", block)
        desc = re.search(r"(?s)<description>(.*?)</description>", block)
        date = re.search(r"(?s)<pubDate>(.*?)</pubDate>", block)
        if not link:
            continue
        items.append(
            {
                "title": _clean(title.group(1) if title else "", 120),
                "url": html.unescape(link.group(1).strip()),
                "snippet": _clean(desc.group(1) if desc else ""),
                "published": html.unescape(date.group(1).strip()) if date else "",
            }
        )
        if len(items) >= limit:
            break
    if items:
        return items

    for block in _BING_ALGO_RE.findall(page):
        anchor = _BING_HREF_RE.search(block)
        if not anchor:
            continue
        para = re.search(r"(?s)<p[^>]*>(.*?)</p>", block)
        items.append(
            {
                "title": _clean(anchor.group(2), 120),
                "url": html.unescape(anchor.group(1)),
                "snippet": _clean(para.group(1) if para else ""),
                "published": "",
            }
        )
        if len(items) >= limit:
            break
    return items


# ------------------------- 检索编排 -------------------------

# 整体预算 8s：两个引擎并发跑，谁先回谁先用；不再串行等 360 超时 + Bing 兜底
_SEARCH_BUDGET = 8.0
_ENGINE_TIMEOUT = {"news": 6.0, "so": 6.0, "bing": 5.0, "bing_html": 4.0}
# 引擎被风控/结构未命中后冷一会儿，避免同一进程里连续几次都白等
_COOLDOWN: dict[str, float] = {}
_COOLDOWN_S = 90
_SCORE_FLOOR = 0.2
# 官方/协会/头部承运商域名，命中给一点权重（不是万灵药，只是排序偏好）
_AUTHORITY_HOSTS = (
    "sse.net.cn",
    "customs.gov.cn",
    "mot.gov.cn",
    "mofcom.gov.cn",
    "gov.cn",
    "cbp.gov",
    "federalregister.gov",
    "ustr.gov",
    "wto.org",
    "unctad.org",
    "imo.org",
    "dhl.com",
    "ups.com",
    "fedex.com",
    "sf-express.com",
    "maersk.com",
    "msc.com",
    "cma-cgm.com",
)


def _cooling(engine: str) -> bool:
    return _COOLDOWN.get(engine, 0) > time.time()


def _cool(engine: str) -> None:
    _COOLDOWN[engine] = time.time() + _COOLDOWN_S


def _run_engine(engine: str, query: str, limit: int) -> list[dict]:
    if engine == "news":
        page = _http_get(
            f"{SEARCH_NEWS_URL}?q={quote(query)}", timeout=min(REALTIME_TIMEOUT, _ENGINE_TIMEOUT["news"])
        )[2]
        return _parse_so_news(page, limit)
    if engine == "so":
        page = _http_get(
            f"{SEARCH_BASE_URL}?q={quote(query)}", timeout=min(REALTIME_TIMEOUT, _ENGINE_TIMEOUT["so"])
        )[2]
        return _parse_so(page, limit)
    if engine == "bing":
        body = _http_get(
            f"{SEARCH_FALLBACK_URL}?q={quote(query)}&format=rss&count={limit}",
            timeout=min(REALTIME_TIMEOUT, _ENGINE_TIMEOUT["bing"]),
        )[2]
        items = _parse_bing(body, limit)
        if items:
            return items
        page = _http_get(
            f"{SEARCH_FALLBACK_URL}?q={quote(query)}", timeout=min(REALTIME_TIMEOUT, _ENGINE_TIMEOUT["bing_html"])
        )[2]
        return _parse_bing(page, limit)
    raise ValueError(f"未知检索引擎：{engine}")


_STOP_TOKENS = {
    "最新", "最新变化", "变化", "情况", "相关", "如何", "什么", "现在", "目前",
    "请", "帮我", "查询", "信息", "一下", "有哪些", "要求", "政策",
}


def _tokens(query: str) -> list[str]:
    """按空白/标点切词并去掉泛词与纯数字（模型常把长句整段塞进 query，全量匹配会一条不中）。"""
    raw = [t.strip() for t in re.split(r"[\s,，、;；]+", query or "") if t.strip()]
    keep = [t for t in raw if t not in _STOP_TOKENS and not t.isdigit() and len(t) >= 2]
    if not keep and query and query.strip():
        return [query.strip()]
    # 最长（通常最具体）的 4 段优先，避免长句把覆盖率摊薄
    keep.sort(key=len, reverse=True)
    return keep[:4]


def _age_days(published: str) -> int | None:
    """能解析就返回距今天数；解析不了返回 None（不奖惩）。"""
    raw = (published or "").strip()
    if not raw:
        return None
    from datetime import date, datetime

    parsed = None
    for pattern in (r"(\d{4})年(\d{1,2})月(\d{1,2})日", r"(\d{4})-(\d{1,2})-(\d{1,2})"):
        m = re.search(pattern, raw)
        if m:
            try:
                parsed = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                parsed = None
            break
    if not parsed:
        m = re.match(r"(\d{2})-(\d{2})\s+\d{1,2}:\d{2}", raw)
        if m:
            try:
                parsed = date(datetime.now().year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                parsed = None
    if not parsed or parsed.year < 2000:
        return None
    return (datetime.now().date() - parsed).days


def _score_item(item: dict, tokens: list[str]) -> float:
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    blob = f"{title} {snippet}"
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in blob)
    title_hits = sum(1 for t in tokens if t in title)
    # 命中 2–3 个关键词就算强相关；不按 token 总数摊薄，否则长句永远不及格
    coverage = min(1.0, hits / min(2.5, max(1.0, len(tokens) * 0.75)))
    title_hit = min(1.0, title_hits / 1.5)
    score = 0.55 * coverage + 0.25 * title_hit
    host = urlparse(item.get("url") or "").netloc.lower()
    if any(a in host for a in _AUTHORITY_HOSTS):
        score += 0.2
    age = _age_days(item.get("published") or "")
    if age is not None:
        if age <= 30:
            score += 0.12
        elif age <= 120:
            score += 0.04
        elif age <= 365:
            score -= 0.3
        else:
            score -= 0.5
    return round(score, 3)


def _title_key(title: str) -> str:
    """去掉站点后缀后再比对，避免同一篇稿子被不同渠道当成不同结果。"""
    text = re.split(r"[_|｜]", title or "", maxsplit=1)[0]
    return re.sub(r"\s+", "", text)[:30]


def _rank(items: list[dict], tokens: list[str]) -> tuple[list[dict], int]:
    """按相关度排序 + 去重 + 过滤跑题；返回 (保留条目, 被丢弃条数)。"""
    scored = []
    dropped = 0
    seen_url: set[str] = set()
    seen_title: set[str] = set()
    for item in items:
        score = _score_item(item, tokens)
        if score < _SCORE_FLOOR:
            dropped += 1
            continue
        url = (item.get("url") or "").strip()
        key = _title_key(item.get("title") or "")
        if url in seen_url or (key and key in seen_title):
            dropped += 1
            continue
        seen_url.add(url)
        if key:
            seen_title.add(key)
        scored.append({**item, "score": score, "host": urlparse(url).netloc.lower()})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored, dropped


# ------------------------- 工具 -------------------------


@register(roles=("quote", "plan", "clearance", "compliance"), name="realtime.web_search")
def search_web(query: str = "", top_k: int = 5) -> dict:
    """联网检索最新公开信息（运价、燃油附加费、港口拥堵、船期、关税与认证政策），返回标题/链接/摘要/发布时间。"""
    q = (query or "").strip()
    if not q:
        raise ValueError("检索关键词为空")
    limit = max(1, min(int(top_k or REALTIME_MAX_RESULTS), 10))

    cache_key = f"search::{q}::{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    tokens = _tokens(q)
    engines = [e.strip().lower() for e in str(SEARCH_ENGINE or "news,so,bing").split(",") if e.strip()]
    engines = [e for e in engines if e in ("news", "so", "bing")]
    errors: list[str] = []
    collected: list[dict] = []
    used: list[str] = []

    # 两个引擎并发；整体 8s 预算，到点就用手上已有的结果
    deadline = time.time() + min(REALTIME_TIMEOUT, _SEARCH_BUDGET)
    candidates = [e for e in engines if not _cooling(e)]
    if len(engines) > len(candidates):
        errors.append("部分引擎处于冷却期（近期被风控/结构未命中）")

    if candidates:
        pool = ThreadPoolExecutor(max_workers=len(candidates))
        futures = {pool.submit(_run_engine, e, q, limit): e for e in candidates}
        try:
            for fut in as_completed(futures, timeout=max(1.0, deadline - time.time())):
                engine = futures[fut]
                try:
                    got = fut.result()
                except Exception as exc:  # noqa: BLE001 单引擎失败不影响另一家
                    _cool(engine)
                    errors.append(f"{engine}: {type(exc).__name__}: {exc}"[:140])
                    continue
                if not got:
                    errors.append(f"{engine}: 无结果（可能被风控或页面改版）")
                    continue
                used.append(engine)
                collected.extend({**item, "engine": engine} for item in got)
                # 已有一家给足候选就别再等慢引擎，把延迟压到单引擎水平
                if len(collected) >= limit * 2:
                    break
        except TimeoutError:
            errors.append("检索超时（引擎响应慢），已用先返回的结果/RSS")
        finally:
            for fut in futures:
                fut.cancel()
            pool.shutdown(wait=False)

    # 检索引擎被风控时会返回百科、外交部国家概况、新闻站首页这类噪声，先按物流语义筛掉
    try:
        from logistics_agent.core.live_feed import _is_junk, _rss_items

        junk_free = [item for item in collected if not _is_junk(item)]
        junk_n = len(collected) - len(junk_free)
        if junk_n:
            errors.append(f"已过滤 {junk_n} 条跑题结果（百科/首页/非物流内容）")
        ranked, dropped = _rank(junk_free, tokens)
        if dropped:
            errors.append(f"相关度不足/重复，丢弃 {dropped} 条")

        if len(ranked) < 2:
            # RSS 补位门槛比引擎结果低：它自带发布时间，且标题常只提到部分关键词
            need = 2 if len(tokens) >= 4 else 1
            rss_pool = []
            for item in _rss_items():
                if _is_junk(item):
                    continue
                text = f"{item.get('title', '')} {item.get('snippet', '')}"
                if sum(1 for t in tokens if t in text) < need:
                    continue
                url = (item.get("url") or "").strip()
                rss_pool.append(
                    {
                        **item,
                        "topic": q,
                        "score": _score_item(item, tokens),
                        "host": urlparse(url).netloc.lower(),
                        "engine": "rss",
                    }
                )
            ranked.extend(rss_pool)
            ranked.sort(key=lambda x: x["score"], reverse=True)
    except Exception as exc:  # noqa: BLE001 过滤/补位失败不影响主检索
        ranked, dropped = collected, 0
        errors.append(f"rss: {type(exc).__name__}: {exc}"[:140])

    if not ranked:
        detail = "；".join(errors[-2:]) if errors else "无可用引擎"
        raise RuntimeError(f"检索未命中：引擎返回跑题内容或不可用（风控/改版），RSS 也无匹配条目（{detail}）")

    ranked = ranked[:limit]
    engines_used = list(dict.fromkeys(i.get("engine") for i in ranked if i.get("engine")))
    payload = {
        "query": q,
        "engine": "+".join(engines_used) or "rss",
        "fetched_at": _now(),
        "count": len(ranked),
        "items": ranked,
        "tokens": tokens,
        "hint": "公开检索结果，多为服务商宣传页或行业资讯，非承运商协议价；引用时必须给出链接与取数时间。",
    }
    if errors:
        payload["degraded"] = errors
    return _cache_put(cache_key, payload)


@register(roles=("quote", "plan", "clearance", "compliance"), name="realtime.fetch_page")
def fetch_page(url: str = "", max_chars: int = 0) -> dict:
    """抓取指定网页正文并转纯文本（用于核实检索结果原文、读取公告/费率页），返回标题与正文摘要。"""
    target = (url or "").strip()
    if not target:
        raise ValueError("缺少 url")
    limit = max(200, min(int(max_chars or FETCH_MAX_CHARS), 8000))

    cache_key = f"fetch::{target}::{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    status, _, page = _http_get(target)
    title_match = _TITLE_RE.search(page)
    title = _clean(title_match.group(1), 160) if title_match else ""

    body = _COMMENT_RE.sub(" ", page)
    body = _BLOCK_TAG_RE.sub(" ", body)
    body = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", body, flags=re.I)
    body = _TAG_RE.sub("", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r" *\n *", "\n", body)
    body = _BLANK_RE.sub("\n\n", body).strip()
    text = body[:limit]

    return _cache_put(
        cache_key,
        {
            "url": target,
            "status": status,
            "title": title,
            "chars": len(text),
            "truncated": len(body) > limit,
            "text": text,
            "fetched_at": _now(),
            "source": urlparse(target).netloc,
        },
    )


@register(roles=("quote", "plan", "clearance", "compliance"), name="realtime.fx_rate")
def fx_rate(base: str = "USD", symbols: str = "CNY,EUR,JPY,GBP,HKD,AUD") -> dict:
    """查询实时汇率（日更，带更新时刻），用于多币种报价与到岸成本的换算。"""
    code = (base or "USD").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"基准货币不合法：{base}")

    cache_key = f"fx::{code}"
    cached = _cache_get(cache_key)
    if cached:
        wanted = _split_symbols(symbols)
        picked = {k: v for k, v in cached["rates"].items() if k in wanted}
        return {**cached, "rates": picked or cached["rates"]}

    _, _, body = _http_get(f"{FX_BASE_URL}/{code}", timeout=min(REALTIME_TIMEOUT, 8))
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"汇率接口返回非 JSON：{body[:120]}") from exc
    if data.get("result") != "success":
        raise RuntimeError(f"汇率接口异常：{data.get('error-type') or data.get('result')}")

    payload = {
        "base": data.get("base_code", code),
        "updated_utc": data.get("time_last_update_utc", ""),
        "updated_unix": data.get("time_last_update_unix", 0),
        "fetched_at": _now(),
        "source": "open.er-api.com",
        "rates": {k: float(v) for k, v in (data.get("rates") or {}).items()},
        "note": "汇率为日更中间价，仅作报价参考；结算以银行/支付渠道实时牌价为准。",
    }
    _cache_put(cache_key, payload)
    wanted = _split_symbols(symbols)
    picked = {k: payload["rates"][k] for k in wanted if k in payload["rates"]}
    return {**payload, "rates": picked or payload["rates"]}


def _split_symbols(symbols: str) -> list[str]:
    return [s.strip().upper() for s in str(symbols or "").split(",") if s.strip()]


# ------------------------- 运价指数 -------------------------

_SCFI_ROW_RE = re.compile(r"(?s)<tr>\s*<td[^>]*>\s*综合指数.*?</tr>")
_SCFI_CELL_RE = re.compile(r'(?s)<td[^>]*align="center"[^>]*>\s*([-\d.]+)\s*</td>')
_SCFI_PERIOD_RE = re.compile(r"(上期|本期)\s*<br\s*/?>\s*(\d{4}-\d{2}-\d{2})")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@register(roles=("quote", "plan", "clearance", "compliance"), name="realtime.scfi_index")
def scfi_index() -> dict:
    """查询上海航运交易所 SCFI 出口集装箱运价指数（最新一期综合指数、上期对比、涨跌幅与期次日期）。"""
    cache_key = "scfi::latest"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    page = _http_get(SCFI_URL, timeout=min(REALTIME_TIMEOUT, 10))[2]
    periods = dict(_SCFI_PERIOD_RE.findall(page))
    row = _SCFI_ROW_RE.search(page)
    if not row:
        raise RuntimeError("SCFI 页面结构未命中（可能改版），请改用 realtime.web_search 检索运价指数")
    cells = _SCFI_CELL_RE.findall(row.group(0))
    numbers = [float(x) for x in cells] if cells else [float(x) for x in _NUM_RE.findall(row.group(0))][:3]
    if len(numbers) < 2:
        raise RuntimeError("SCFI 页面未解析到指数数值（分航线数据可能由前端异步加载）")

    prev, cur = numbers[0], numbers[1]
    change = numbers[2] if len(numbers) > 2 else round(cur - prev, 2)
    change_pct = round((change / prev) * 100, 2) if prev else 0.0

    payload = {
        "index": "SCFI 上海出口集装箱运价指数（综合）",
        "unit": "点",
        "previous": {"period": periods.get("上期", ""), "value": prev},
        "current": {"period": periods.get("本期", ""), "value": cur},
        "change": change,
        "change_pct": change_pct,
        "trend": "上涨" if change > 0 else "下跌" if change < 0 else "持平",
        "source": SCFI_URL,
        "fetched_at": _now(),
        "note": "分航线（欧洲/美西/美东等）在官网由前端异步加载，本工具只取综合指数；具体航线请以官网当期表为准。",
    }
    return _cache_put(cache_key, payload)


__all__ = ["search_web", "fetch_page", "fx_rate", "scfi_index"]
