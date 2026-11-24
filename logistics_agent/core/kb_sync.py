# -*- coding: utf-8 -*-
"""知识库周更：每周生成「候选更新」周报，人工确认后才写盘（写前自动备份）。

范围（按使用方口径）：
- 行情基线：SCFI 综合指数 + 主要货币汇率 → `data/knowledge/market_baseline.json`，
  实时源挂掉时作为兜底，也是"这周市场什么价"的存档。
- 口岸时效：`ports.json` 的 `congestion_days`，依据当周公开资讯的拥堵口径做 ±0.3 微调（低置信）。

流程：build_proposal() → `data/knowledge/weekly/<stamp>.{json,md}` → apply_latest(confirm=True)
→ 备份到 `data/knowledge/_backup/<stamp>/` → 覆盖对应字段 → 清知识库 lru_cache。
"""

import json
import re
import shutil
import threading
import time
from pathlib import Path

from config import KB_SYNC_ENABLED, KB_SYNC_HOUR, KB_SYNC_WEEKDAY, PATHS

KNOWLEDGE = Path(PATHS["knowledge"])
WEEKLY = KNOWLEDGE / "weekly"
BACKUP = KNOWLEDGE / "_backup"
MARKET_FILE = KNOWLEDGE / "market_baseline.json"
PORTS_FILE = Path(PATHS["ports"])

MARKET_FIELDS = {"base": "USD", "symbols": "CNY,EUR,JPY,GBP,HKD"}
PORT_STEP = 0.3  # 单次调整步长（点/天），避免一周内被一条新闻带偏太多
PORT_MIN, PORT_MAX = 0.5, 8.0

_NEG = ("加剧", "恶化", "严重", "罢工", "瘫痪", "拥堵升级", "延误", "积压", "排队", "停摆", "绕行")
_POS = ("缓解", "改善", "恢复", "下降", "疏通", "减少", "结束", "好转", "缓和", "消退", "回落", "放宽")

_MARKET_NOTE = "每周更新的行情基线；实时源不可用时由 live_feed 兜底引用，口径以当期官网为准。"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _stamp() -> str:
    return time.strftime("%Y-%m-%d_%H%M", time.localtime())


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _latest_path() -> Path:
    return WEEKLY / "latest.json"


def _port_stem(name: str) -> str:
    """「盐田港（深圳）」→「盐田」；用于在资讯标题里做粗匹配。"""
    return str(name).split("（")[0].replace("港", "").strip()


def _sentiment(text: str) -> int:
    score = 0
    for word in _NEG:
        if word in text:
            score += 1
    for word in _POS:
        if word in text:
            score -= 1
    return max(-1, min(1, score))


def _market_payload() -> tuple[dict | None, list[str]]:
    from logistics_agent.core.live_feed import market

    errors: list[str] = []
    try:
        live = market(force=True)
    except Exception as exc:  # noqa: BLE001
        return None, [f"market: {type(exc).__name__}: {exc}"[:160]]
    errors.extend(live.get("errors") or [])

    scfi = live.get("scfi")
    fx = live.get("fx")
    if not scfi and not fx:
        return None, errors or ["行情两路都失败"]

    proposed = {"as_of": _now()}
    if scfi:
        proposed["scfi"] = {
            "value": (scfi.get("current") or {}).get("value"),
            "period": (scfi.get("current") or {}).get("period"),
            "previous": (scfi.get("previous") or {}).get("value"),
            "change": scfi.get("change"),
            "change_pct": scfi.get("change_pct"),
            "trend": scfi.get("trend"),
            "source": scfi.get("source") or "上海航运交易所",
        }
    if fx:
        rates = fx.get("rates") or {}
        proposed["fx"] = {
            "base": fx.get("base") or MARKET_FIELDS["base"],
            "rates": {k: rates.get(k) for k in MARKET_FIELDS["symbols"].split(",") if rates.get(k)},
            "updated_utc": fx.get("updated_utc"),
            "source": fx.get("source") or "open.er-api.com",
        }
    return proposed, errors


def _too_old(published: str, days: int = 120) -> bool:
    """发布时间能解析且超过 days 天的旧文不作为本周依据；解析不了就放行（置信已标 low）。"""
    raw = (published or "").strip()
    if not raw:
        return False
    from datetime import date, datetime, timedelta

    parsed: date | None = None
    for pattern in (r"(\d{4})年(\d{1,2})月(\d{1,2})日", r"(\d{4})-(\d{1,2})-(\d{1,2})"):
        m = re.search(pattern, raw)
        if m:
            try:
                parsed = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                parsed = None
            break
    if not parsed:
        m = re.match(r"(\d{2})-(\d{2})\s+\d{1,2}:\d{2}", raw)  # RSS 的「MM-DD HH:MM」，按当年算
        if m:
            try:
                parsed = date(datetime.now().year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                parsed = None
    if not parsed or parsed.year < 2000:
        return False
    return (datetime.now().date() - parsed) > timedelta(days=days)


def _port_changes(items: list[dict]) -> list[dict]:
    ports_doc = _read(PORTS_FILE, {})
    ports = ports_doc.get("ports", [])
    all_names = [p.get("name", "") for p in ports] + [_port_stem(p.get("name", "")) for p in ports]

    changes: list[dict] = []
    for port in ports:
        name = port.get("name", "")
        stem = _port_stem(name)
        evidence = []
        for item in items:
            title = item.get("title", "")
            # 只看标题命中：摘要里顺带提到某港口的多是"哪家更堵"的横向对比，方向会串
            if not (name in title or (stem and stem in title)):
                continue
            if _too_old(item.get("published", "")):
                continue
            # 一条标题里点了两个以上口岸，说明是横向对比稿，不作为单一口岸的方向依据
            mentioned = sum(1 for other in all_names if other and other in title)
            if mentioned >= 2 and name not in title[: len(name) + 6]:
                continue
            evidence.append(item)
        if not evidence:
            continue

        score = 0
        for item in evidence:
            score += _sentiment(f"{item.get('title', '')} {item.get('snippet', '')}")
        if score == 0:
            continue

        current = float(port.get("congestion_days", 1.0))
        proposed = round(max(PORT_MIN, min(PORT_MAX, current + (PORT_STEP if score > 0 else -PORT_STEP))), 1)
        if proposed == current:
            continue
        changes.append(
            {
                "code": port.get("code"),
                "name": name,
                "country": port.get("country"),
                "field": "congestion_days",
                "current": current,
                "proposed": proposed,
                "delta": round(proposed - current, 1),
                "confidence": "medium" if len(evidence) >= 2 else "low",
                "reason": "本周资讯里该口岸以「拥堵加剧/延误」为主" if score > 0 else "本周资讯里该口岸以「缓解/恢复」为主",
                "evidence": [
                    {"title": (e.get("title") or "")[:80], "url": e.get("url"), "published": e.get("published")}
                    for e in evidence[:2]
                ],
            }
        )
    return changes


def _category_payload() -> dict:
    """周更的品类复核：重检已确认的自动收录条目，并列出过期待审候选。

    只出结论，不写盘——真正落盘在 apply_latest()；未确认候选永远不进匹配。
    """
    from logistics_agent.core import kb_ingest

    errors: list[str] = []
    confirmed = kb_ingest.confirmed_items()
    verify: list[dict] = []
    changes: list[dict] = []
    missing: list[dict] = []

    for item in confirmed[:6]:
        name = item.get("name") or ""
        items, err = kb_ingest._search(f"{name} HS编码 出口", limit=3)
        errors.extend(err)
        items = kb_ingest._relevant(items, name)
        evidence = [
            {"title": (i.get("title") or "")[:80], "url": i.get("url"), "published": i.get("published")}
            for i in items[:2]
        ]
        if not items:
            missing.append(
                {
                    "hs": item.get("hs"),
                    "name": name,
                    "miss_streak": int(item.get("miss_streak", 0)) + 1,
                    "reason": "本周检索不到该品类的公开 HS 依据",
                }
            )
            continue
        codes = kb_ingest._extract_codes(items)
        top_code, top_score = codes[0] if codes else ("", 0)
        if top_code and top_code != item.get("hs") and top_score >= 6:
            changes.append(
                {
                    "current": item.get("hs"),
                    "proposed": top_code,
                    "name": name,
                    "confidence": "low",
                    "reason": "当周公开资讯里高频指向另一 HS 编码，需人工复核税则差异",
                    "evidence": evidence,
                }
            )
        else:
            verify.append({"hs": item.get("hs"), "name": name, "evidence": evidence, "checked_at": _now()})

    pending = kb_ingest.list_candidates("pending", 200)
    prune = [
        c
        for c in pending
        if kb_ingest._age_hours(c.get("added_at")) > KB_CANDIDATE_TTL_DAYS * 24
        and int(c.get("observed_count", 0)) <= 1
    ]

    return {
        "checked_at": _now(),
        "confirmed_count": len(confirmed),
        "pending_count": len(pending),
        "verify": verify,
        "hs_changes": changes,
        "missing": missing,
        "prune_pending": [{"id": c.get("id"), "name": c.get("name"), "added_at": c.get("added_at")} for c in prune],
        "errors": list(dict.fromkeys(errors))[:6],
    }


def _report_md(proposal: dict) -> str:
    market = (proposal.get("market") or {}).get("proposed") or {}
    scfi = market.get("scfi") or {}
    fx = (market.get("fx") or {}).get("rates") or {}
    lines = [
        f"# 知识库周报 · {proposal['stamp']}",
        "",
        f"- 生成时间：{proposal['generated_at']}（触发：{proposal['trigger']}）",
        f"- 范围：行情基线 + ports.json 的 congestion_days + 自动收录品类复核",
        f"- 说明：本文件只是候选更新，未写盘；确认后由 `/api/kb/apply` 落盘并备份。",
        "",
        "## 一、行情基线",
        "",
    ]
    if scfi:
        lines.append(
            f"- SCFI 综合：**{scfi.get('value')} 点**（期次 {scfi.get('period')}），"
            f"上期 {scfi.get('previous')}，环比 {scfi.get('change')} / {scfi.get('change_pct')}%，{scfi.get('trend') or ''}"
        )
    else:
        lines.append("- SCFI：本周取数失败（保留原基线）")
    if fx:
        lines.append("- 汇率（USD）：" + "，".join(f"{k} {v}" for k, v in fx.items()))
    else:
        lines.append("- 汇率：本周取数失败（保留原基线）")

    lines += ["", "## 二、口岸时效候选", ""]
    changes = (proposal.get("ports") or {}).get("proposed") or []
    if not changes:
        lines.append("本周资讯未给出明确的口岸拥堵变化信号，`ports.json` 无候选调整。")
    else:
        lines.append("| 口岸 | 代码 | 当前 | 候选 | 变化 | 置信 | 依据 |")
        lines.append("|---|---|---|---|---|---|---|")
        for c in changes:
            ev = c["evidence"][0] if c["evidence"] else {}
            link = f"[{ev.get('title', '')[:28]}]({ev.get('url', '')})" if ev.get("url") else ""
            lines.append(
                f"| {c['name']} | {c['code']} | {c['current']} | {c['proposed']} | "
                f"{c['delta']:+} | {c['confidence']} | {c['reason']} {link} |"
            )

    cat = proposal.get("categories") or {}
    checked = len(cat.get("verify", [])) + len(cat.get("hs_changes", [])) + len(cat.get("missing", []))
    lines += ["", "## 三、自动收录品类复核", ""]
    if not cat.get("confirmed_count") and not cat.get("pending_count"):
        lines.append("本周知识库没有自动收录的品类，也没有待审候选。")
    else:
        lines.append(
            f"- 已确认条目 {cat.get('confirmed_count', 0)} 个，本周复核 {checked} 个；"
            f"待审候选 {cat.get('pending_count', 0)} 个（未确认前不参与匹配与税费测算）"
        )
        if cat.get("hs_changes"):
            lines += ["", "| 品类 | 原 HS | 候选 HS | 置信 | 依据 |", "|---|---|---|---|---|"]
            for c in cat["hs_changes"]:
                ev = (c.get("evidence") or [{}])[0]
                link = f"[{(ev.get('title') or '')[:26]}]({ev.get('url')})" if ev.get("url") else ""
                lines.append(f"| {c['name']} | {c['current']} | {c['proposed']} | {c['confidence']} | {c['reason']} {link} |")
        if cat.get("missing"):
            lines += ["", "本周复核无证据（连续 3 周将停用）：" + "、".join(
                f"{m['name']}（miss_streak={m['miss_streak']}）" for m in cat["missing"]
            )]
        if cat.get("prune_pending"):
            lines += ["", "过期待审候选（确认后清理）：" + "、".join(
                f"{p.get('name') or p.get('id')}（{p.get('added_at', '')[:10]}）" for p in cat["prune_pending"]
            )]
        if not cat.get("hs_changes") and not cat.get("missing") and not cat.get("prune_pending"):
            lines.append("- 本周复核未见 HS 冲突或无证据条目，证据已刷新。")

    errors = proposal.get("errors") or []
    if errors:
        lines += ["", "## 四、降级记录", ""]
        lines += [f"- {e}" for e in errors[:6]]
    return "\n".join(lines) + "\n"


def build_proposal(trigger: str = "manual") -> dict:
    """抓行情 + 口岸资讯，生成本周候选更新（只写 weekly/ 下的周报，不动知识库本体）。"""
    WEEKLY.mkdir(parents=True, exist_ok=True)
    market_proposed, errors = _market_payload()
    current_market = _read(MARKET_FILE, {})

    try:
        from logistics_agent.core.live_feed import news

        # 只盯当周拥堵最重的前 5 个口岸：既省请求，也最可能有可核实的资讯
        ports_doc = _read(PORTS_FILE, {})
        hot = sorted(
            ports_doc.get("ports", []), key=lambda p: float(p.get("congestion_days", 1.0)), reverse=True
        )[:5]
        queries = [f"{p.get('name', '')} 拥堵 最新" for p in hot if p.get("name")]
        queries.append("港口 拥堵 最新")
        items, news_errors = news(queries, limit=3)
        errors.extend(news_errors)
    except Exception as exc:  # noqa: BLE001
        items = []
        errors.append(f"news: {type(exc).__name__}: {exc}"[:160])

    port_changes = _port_changes(items)

    try:
        categories = _category_payload()
        errors.extend(categories.get("errors") or [])
    except Exception as exc:  # noqa: BLE001 品类复核失败不影响其它板块出周报
        categories = {"checked_at": _now(), "confirmed_count": 0, "pending_count": 0, "verify": [], "hs_changes": [], "missing": [], "prune_pending": [], "errors": []}
        errors.append(f"categories: {type(exc).__name__}: {exc}"[:160])

    market_changed = False
    if market_proposed:
        old = {
            "scfi": (current_market.get("scfi") or {}).get("value"),
            "period": (current_market.get("scfi") or {}).get("period"),
            "cny": ((current_market.get("fx") or {}).get("rates") or {}).get("CNY"),
        }
        new = {
            "scfi": (market_proposed.get("scfi") or {}).get("value"),
            "period": (market_proposed.get("scfi") or {}).get("period"),
            "cny": ((market_proposed.get("fx") or {}).get("rates") or {}).get("CNY"),
        }
        market_changed = old != new

    stamp = _stamp()
    proposal = {
        "stamp": stamp,
        "generated_at": _now(),
        "trigger": trigger,
        "scope": ["market_baseline", "ports.congestion_days", "hs_codes.auto_items"],
        "market": {"proposed": market_proposed, "current": current_market, "changed": market_changed},
        "ports": {"proposed": port_changes, "count": len(port_changes), "news_scanned": len(items)},
        "categories": categories,
        "errors": list(dict.fromkeys(errors)),
        "applied_at": None,
    }
    proposal_path = WEEKLY / f"{stamp}.json"
    report_path = WEEKLY / f"{stamp}.md"
    try:
        proposal["report"] = str(report_path.relative_to(KNOWLEDGE.parent.parent))
    except ValueError:
        proposal["report"] = str(report_path)
    _write(proposal_path, proposal)
    report_path.write_text(_report_md(proposal), encoding="utf-8")
    _write(_latest_path(), proposal)

    from logistics_agent.core.telemetry import live

    live.log("kb_proposal", stamp=stamp, trigger=trigger, ports=len(port_changes), market_ok=bool(market_proposed))
    return proposal


def latest_proposal() -> dict | None:
    return _read(_latest_path(), None)


def history(limit: int = 12) -> list[dict]:
    if not WEEKLY.exists():
        return []
    rows: list[dict] = []
    for path in sorted(WEEKLY.glob("*.json"), reverse=True):
        if path.name == "latest.json":
            continue
        data = _read(path, None)
        if not data:
            continue
        rows.append(
            {
                "stamp": data.get("stamp"),
                "trigger": data.get("trigger"),
                "ports": (data.get("ports") or {}).get("count", 0),
                "market_ok": bool(data.get("market", {}).get("proposed")),
                "applied_at": data.get("applied_at"),
            }
        )
        if len(rows) >= max(1, min(limit, 60)):
            break
    return rows


def apply_latest(confirm: bool = False) -> dict:
    """把最新周报落盘：先备份，再改 ports.json / market_baseline.json / 品类 auto_items。"""
    if not confirm:
        return {"ok": False, "error": "写操作需要 confirm=true（本周报不会自动改表）"}
    proposal = latest_proposal()
    if not proposal:
        return {"ok": False, "error": "还没有周报，请先生成（POST /api/kb/run）"}
    if proposal.get("applied_at"):
        return {"ok": False, "error": f"该周报已于 {proposal['applied_at']} 写入，如需重跑请重新生成"}

    backup_dir = BACKUP / time.strftime("%Y%m%d_%H%M%S", time.localtime())
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PORTS_FILE, backup_dir / "ports.json")
    if MARKET_FILE.exists():
        shutil.copy2(MARKET_FILE, backup_dir / "market_baseline.json")
    from logistics_agent.core import kb_ingest

    if Path(PATHS["hs_codes"]).exists():
        shutil.copy2(PATHS["hs_codes"], backup_dir / "hs_codes.json")
    candidates_file = Path(PATHS["knowledge"]) / "hs_candidates.json"
    if candidates_file.exists():
        shutil.copy2(candidates_file, backup_dir / "hs_candidates.json")

    applied_ports = 0
    changes = (proposal.get("ports") or {}).get("proposed") or []
    if changes:
        ports_doc = _read(PORTS_FILE, {})
        by_code = {c["code"]: c for c in changes}
        for port in ports_doc.get("ports", []):
            change = by_code.get(port.get("code"))
            if change:
                port["congestion_days"] = change["proposed"]
                applied_ports += 1
        meta = ports_doc.setdefault("_meta", {})
        meta["updated_at"] = _now()
        meta["note"] = "口岸/机场拥堵基线，congestion_days 会叠加到清关时效预估；每周由 /api/kb/apply 确认后更新。"
        _write(PORTS_FILE, ports_doc)

    market = (proposal.get("market") or {}).get("proposed")
    market_written = False
    if market:
        payload = {"_meta": {"note": _MARKET_NOTE, "updated_at": _now(), "source": "weekly kb_sync"}}
        payload.update(market)
        _write(MARKET_FILE, payload)
        market_written = True

    category_stats: dict = {}
    try:
        category_stats = kb_ingest.apply_weekly(proposal.get("categories") or {})
    except Exception as exc:  # noqa: BLE001 品类落盘失败也要给出原因，不静默
        category_stats = {"error": f"{type(exc).__name__}: {exc}"[:160]}

    # 知识库用 lru_cache 缓存，落盘后必须失效，否则旧值要重启才生效
    try:
        from logistics_agent.tools.knowledge import _load as _kb_load

        _kb_load.cache_clear()
    except Exception:  # noqa: BLE001
        pass

    proposal["applied_at"] = _now()
    proposal["applied_backup"] = str(backup_dir.relative_to(KNOWLEDGE))
    _write(WEEKLY / f"{proposal['stamp']}.json", proposal)
    _write(_latest_path(), proposal)

    from logistics_agent.core.telemetry import live

    live.log("kb_apply", stamp=proposal["stamp"], ports=applied_ports, market=market_written, categories=category_stats)
    return {
        "ok": True,
        "stamp": proposal["stamp"],
        "ports_applied": applied_ports,
        "market_written": market_written,
        "categories": category_stats,
        "backup": str(backup_dir),
    }


def _due() -> bool:
    now = time.localtime()
    if now.tm_wday != KB_SYNC_WEEKDAY or now.tm_hour < KB_SYNC_HOUR:
        return False
    latest = _latest_path()
    if not latest.exists():
        return True
    return (time.time() - latest.stat().st_mtime) > 6 * 86400


def _loop() -> None:
    while True:
        try:
            if _due():
                build_proposal("scheduler")
        except Exception:  # noqa: BLE001 定时任务不能把进程带崩
            pass
        time.sleep(600)


def start_scheduler() -> bool:
    """服务启动时调用：每周固定日/时点后自动生成周报（只生成，不写盘）。"""
    if not KB_SYNC_ENABLED:
        return False
    threading.Thread(target=_loop, daemon=True).start()
    return True


__all__ = ["build_proposal", "latest_proposal", "history", "apply_latest", "start_scheduler"]
