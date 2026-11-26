# -*- coding: utf-8 -*-
"""通用网页抓取工具：读取公网公开页面，支持单页抓取与同域小规模爬取。

合规：只读公开页面，带真实 UA，遵守 robots.txt，不登录、不提交表单、不绕验证码。
安全：拒绝内网/回环/链路本地地址，限制跳转、响应体积与抓取页数，避免被当成任意代理。
"""

import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from logistics_agent.core.toolkit.decorator import register

_TIMEOUT = 10.0
_MAX_BYTES = 1_500_000
_MAX_REDIRECTS = 5
_MAX_PAGES = 5
_UA = "Mozilla/5.0 (compatible; LogisticsEngineBot/1.0; public-page-reader)"

# 明显不是正文的资源，抓取时直接跳过
_SKIP_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf",
    ".zip", ".rar", ".7z", ".mp4", ".mp3", ".css", ".js", ".woff", ".woff2",
)


def _is_public_host(host: str) -> bool:
    """解析域名并确认所有地址都是公网 IP，挡住内网 / 回环 / 链路本地。"""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _robots_allows(client: httpx.Client, url: str) -> bool:
    """极简 robots.txt 判定：只看 User-agent: * 的 Disallow，取不到就放行。"""
    parsed = urlparse(url)
    robots = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = client.get(robots, timeout=5)
    except Exception:  # noqa: BLE001  拿不到 robots 不影响抓取
        return True
    if resp.status_code >= 400:
        return True
    path = parsed.path or "/"
    applies = False
    for line in resp.text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            applies = value == "*"
        elif applies and key == "disallow" and value and path.startswith(value):
            return False
    return True


def _read_with_limit(client: httpx.Client, url: str) -> tuple[int, str, bytes]:
    """流式读取，超过体积上限即停止，返回状态码 / content-type / 原始字节。"""
    with client.stream("GET", url) as resp:
        ctype = resp.headers.get("content-type", "").lower()
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_BYTES:
                break
        return resp.status_code, ctype, b"".join(chunks)[:_MAX_BYTES]


def _soup_text(raw: bytes) -> BeautifulSoup:
    """解析 HTML，去掉脚本样式等噪音节点。"""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    return soup


def _links_of(soup: BeautifulSoup, base: str, limit: int = 40) -> list[dict]:
    """抽取页面里的绝对链接（去重、跳过资源文件）。"""
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base, href)
        low = url.lower()
        if urlparse(url).scheme not in ("http", "https") or low.endswith(_SKIP_EXT):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"text": a.get_text(" ", strip=True)[:60], "url": url})
        if len(out) >= limit:
            break
    return out


def _fetch_document(client: httpx.Client, url: str) -> dict:
    """抓一页并解析成标题 / 简介 / 小标题 / 正文 / 链接。"""
    status, ctype, raw = _read_with_limit(client, url)
    soup = _soup_text(raw)
    title = soup.title.get_text(strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = (meta.get("content") or "").strip() if meta else ""
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])][:20]
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return {
        "ok": status < 400,
        "status": status,
        "content_type": ctype,
        "title": title,
        "description": description,
        "headings": headings,
        "text": text,
        "links": _links_of(soup, url),
    }


@register(roles=["quote", "plan", "compliance"], name="web.fetch_page")
def fetch_page(url: str, max_chars: int = 2000, include_links: bool = False) -> dict:
    """抓取一个公网公开网页，返回标题、简介、小标题与正文摘要。"""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("只支持 http/https 公网网址")
    if not _is_public_host(parsed.hostname):
        raise ValueError("拒绝访问内网、回环或无法解析的地址")

    limit = max(200, min(int(max_chars or 2000), 6000))
    fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    with httpx.Client(headers=headers, follow_redirects=True, max_redirects=_MAX_REDIRECTS) as client:
        if not _robots_allows(client, url):
            return {"ok": False, "url": url, "error": "robots.txt 禁止抓取该路径", "fetched_at": fetched_at}
        try:
            doc = _fetch_document(client, url)
        except Exception as exc:  # noqa: BLE001  网络错误要变成模型可读的结果
            return {"ok": False, "url": url, "error": f"请求失败：{exc}", "fetched_at": fetched_at}

    text = doc["text"]
    result = {
        "ok": doc["ok"],
        "status": doc["status"],
        "url": str(url),
        "title": doc["title"],
        "description": doc["description"],
        "headings": doc["headings"],
        "text": text[:limit],
        "truncated": len(text) > limit,
        "fetched_at": fetched_at,
        "note": "公开页面内容，可能随时间变化；引用时应注明来源与抓取时间",
    }
    if include_links:
        result["links"] = doc["links"]
    return result


def _norm(url: str) -> str:
    """归一化 URL 用于去重：去掉末尾斜杠。"""
    return url.rstrip("/") or url


@register(roles=["plan", "quote"], name="web.crawl")
def crawl(url: str, keyword: str = "", max_pages: int = 3, max_chars: int = 1200) -> dict:
    """从起始页出发，同域抓取最多 5 个公开页面，可按关键词筛链接并截取相关段落。"""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("只支持 http/https 公网网址")
    if not _is_public_host(parsed.hostname):
        raise ValueError("拒绝访问内网、回环或无法解析的地址")

    pages_limit = max(1, min(int(max_pages or 3), _MAX_PAGES))
    limit = max(200, min(int(max_chars or 1200), 3000))
    host = parsed.hostname.lower()
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    pages, errors, visited = [], [], set()

    with httpx.Client(headers=headers, follow_redirects=True, max_redirects=_MAX_REDIRECTS) as client:
        if not _robots_allows(client, url):
            return {"start": url, "pages": [], "errors": ["robots.txt 禁止抓取该路径"], "visited": 0}

        queue = [url]
        while queue and len(pages) < pages_limit:
            current = queue.pop(0)
            if _norm(current) in visited:
                continue
            visited.add(_norm(current))
            try:
                doc = _fetch_document(client, current)
            except Exception as exc:  # noqa: BLE001
                errors.append({"url": current, "error": str(exc)})
                continue

            text = doc["text"]
            excerpt = text[:limit]
            matched = False
            if keyword:
                pos = text.find(keyword)
                if pos >= 0:
                    matched = True
                    start = max(0, pos - limit // 3)
                    excerpt = text[start:start + limit]
            pages.append(
                {
                    "url": current,
                    "title": doc["title"],
                    "status": doc["status"],
                    "excerpt": excerpt,
                    "matched": matched,
                }
            )

            # 同域链接入队：有关键词时优先关键词相关链接
            candidates = [
                item for item in doc["links"]
                if (urlparse(item["url"]).hostname or "").lower() == host
                and _norm(item["url"]) not in visited
            ]
            if keyword:
                candidates.sort(key=lambda x: keyword not in x["text"] and keyword not in x["url"])
            queue.extend(item["url"] for item in candidates[:5])
            time.sleep(0.3)  # 轻微节流，避免给站点造成压力

    return {
        "start": url,
        "keyword": keyword,
        "pages": pages,
        "errors": errors,
        "visited": len(visited),
        "note": "仅抓取同域公开页面；内容可能变化，引用需注明来源与时间",
    }


__all__ = ["fetch_page", "crawl"]
