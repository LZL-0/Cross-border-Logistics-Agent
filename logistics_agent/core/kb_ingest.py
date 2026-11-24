# -*- coding: utf-8 -*-
"""品类候选收录：知识库没收录的品类，联网检索后存为「待审候选」，人工确认才进库。

为什么单独一层：
- `hs_codes.json` 是人工维护的演示基线，自动抓来的数据不能直接混进去改口径；
- 候选只做「可查、可引用、可追溯来源」，不参与 HS 匹配与税费测算，
  确认后才写入 `hs_codes.json` 的 `auto_items`（与基线同结构，带来源字段）。

文件：`data/knowledge/hs_candidates.json`
- `items`：pending（待审）/ rejected（已忽略）两类，确认成功后移到 `hs_codes.json`；
- 候选带 TTL（默认 30 天）：过期且多次复核无证据的，由周更流程在确认后才清理。
"""

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from config import KB_AUTO_INGEST, KB_CANDIDATE_TTL_DAYS, PATHS
from logistics_agent.tools.catalog import file_lock

KNOWLEDGE = Path(PATHS["knowledge"])
HS_FILE = Path(PATHS["hs_codes"])
CANDIDATE_FILE = KNOWLEDGE / "hs_candidates.json"
BACKUP = KNOWLEDGE / "_backup"

# 同一关键词的联网冷却：避免一次对话里反复触发检索
COOLDOWN_SECONDS = 600
_THROTTLE: dict[str, float] = {}

_CERT_WORDS = ("CE", "FCC", "FDA", "REACH", "RoHS", "ROHS", "UN38.3", "MSDS", "CCC", "UL", "PSE", "KC")
_DG_WORDS = ("电池", "锂", "电芯", "充电", "battery", "lithium", "power bank")
_HS_PATTERN = re.compile(r"(?<!\d)(\d{6,10})(?!\d)")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(text: str) -> str:
    """归一化用于去重比对：小写、去空白与常见标点。"""
    return re.sub(r"[\s\-_/、，,。.（）()]+", "", str(text or "").lower())


def _cand_id(name: str) -> str:
    return "cand_" + hashlib.sha1(_norm(name).encode("utf-8")).hexdigest()[:8]


# ------------------------- 读写 -------------------------

def _doc() -> dict:
    data = _read(CANDIDATE_FILE, None) or {}
    data.setdefault("_meta", {})
    data.setdefault("items", [])
    return data


def _save(doc: dict) -> None:
    doc.setdefault("_meta", {})
    doc["_meta"].update(
        {
            "note": "自动收录的品类候选；pending 不参与匹配与税费测算，确认后写入 hs_codes.json 的 auto_items。",
            "ttl_days": KB_CANDIDATE_TTL_DAYS,
            "updated_at": _now(),
        }
    )
    with file_lock(CANDIDATE_FILE):
        _write(CANDIDATE_FILE, doc)


def _confirmed_doc() -> dict:
    return _read(HS_FILE, {}) or {}


def confirmed_items() -> list[dict]:
    """已确认的自动收录条目（hs_codes.json 的 auto_items，排除已停用）。"""
    return [x for x in _confirmed_doc().get("auto_items", []) if not x.get("disabled")]


# ------------------------- 匹配 -------------------------

def _hit(candidate: dict, keyword: str) -> bool:
    low = _norm(keyword)
    if not low:
        return False
    names = [candidate.get("name", "")] + list(candidate.get("keywords", []))
    for name in names:
        n = _norm(name)
        if n and (n in low or low in n):
            return True
    return False


def find_pending(keyword: str) -> list[dict]:
    """未确认候选里，按品名/关键词做粗匹配。"""
    return [c for c in _doc().get("items", []) if c.get("status") == "pending" and _hit(c, keyword)]


def find_rejected(keyword: str) -> list[dict]:
    return [c for c in _doc().get("items", []) if c.get("status") == "rejected" and _hit(c, keyword)]


def candidate_brief(candidate: dict) -> dict:
    return {
        "id": candidate.get("id"),
        "name": candidate.get("name"),
        "hs": candidate.get("hs") or "",
        "hs_options": candidate.get("hs_options", []),
        "confidence": candidate.get("confidence", "low"),
        "status": candidate.get("status"),
        "added_at": candidate.get("added_at"),
        "observed_count": candidate.get("observed_count", 0),
        "note": candidate.get("notes", ""),
        "evidence": (candidate.get("evidence") or [])[:3],
    }


# ------------------------- 检索解析 -------------------------

def _valid_hs(code: str) -> bool:
    """中国海关税则用 8/10 位：6 位国际码在检索页里极少单独出现，9 位多是日期/编号噪声。"""
    if len(code) not in (8, 10):
        return False
    head = int(code[:2])
    return 1 <= head <= 97


def _extract_codes(items: list[dict]) -> list[tuple[str, int]]:
    """从标题/摘要里抽 HS 编码，标题权重更高。"""
    score: Counter[str] = Counter()
    for item in items:
        title = item.get("title") or ""
        snippet = item.get("snippet") or ""
        for code in _HS_PATTERN.findall(title):
            if _valid_hs(code):
                score[code] += 3
        for code in _HS_PATTERN.findall(snippet):
            if _valid_hs(code):
                score[code] += 1
    return score.most_common(4)


def _certs(text: str) -> list[str]:
    upper = text.upper()
    return [w for w in _CERT_WORDS if w.upper() in upper]


def _is_dg(text: str) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in _DG_WORDS)


def _relevant(items: list[dict], name: str) -> list[dict]:
    """只留标题/摘要里真的提到该品类的条目：检索结果里常混进同日其它新闻。"""
    key = _norm(name)
    toks = _tokens(name)
    out = []
    for item in items:
        text = _norm(f"{item.get('title') or ''} {item.get('snippet') or ''}")
        if (key and key in text) or any(t and t in text for t in toks):
            out.append(item)
    return out


def _risk(text: str) -> str:
    if any(w in text for w in ("危险品", "易燃", "爆炸", "管制", "违禁")):
        return "high"
    if _is_dg(text) or any(w in text for w in ("电子", "电器", "食品接触", "化妆品", "医疗")):
        return "medium"
    return "low"


def _search(query: str, limit: int = 5) -> tuple[list[dict], list[str]]:
    from logistics_agent.tools.realtime import search_web

    try:
        payload = search_web(query, top_k=limit)
        return list(payload.get("items") or []), list(payload.get("degraded") or [])
    except Exception as exc:  # noqa: BLE001 检索失败就当没查到，不能把查询链路带崩
        return [], [f"search: {type(exc).__name__}: {exc}"[:140]]


# ------------------------- 收录 -------------------------

def ingest(keyword: str, trigger: str = "lookup") -> dict:
    """未收录品类 → 联网检索 → 存候选。返回候选摘要，任何异常都不向上抛。"""
    raw = str(keyword or "").strip()
    if not KB_AUTO_INGEST:
        return {"ok": False, "skipped": "KB_AUTO_INGEST 关闭"}
    if len(_norm(raw)) < 2:
        return {"ok": False, "skipped": "关键词过短，无法检索"}

    pending = find_pending(raw)
    if pending:
        fresh = [c for c in pending if _age_hours(c.get("updated_at")) < 24]
        if fresh:
            return {"ok": True, "cached": True, "candidates": [candidate_brief(c) for c in fresh[:2]], "errors": []}

    rejected = find_rejected(raw)
    if rejected and _age_hours(rejected[0].get("updated_at")) < 7 * 24:
        return {"ok": False, "skipped": "该品类刚被人工忽略，7 天内不再自动检索", "candidates": []}

    key = _norm(raw)
    now_ts = time.time()
    if now_ts - _THROTTLE.get(key, 0) < COOLDOWN_SECONDS:
        return {"ok": False, "skipped": "同一关键词 10 分钟内已检索过", "candidates": []}
    _THROTTLE[key] = now_ts

    queries = [f"{raw} HS编码 出口", f"{raw} 海关编码 关税"]
    items, errors = _search(queries[0], limit=5)
    if not items or not _extract_codes(items):
        extra, extra_errors = _search(queries[1], limit=5)
        errors.extend(extra_errors)
        seen = {(i.get("url") or "") + (i.get("title") or "") for i in items}
        items.extend(i for i in extra if (i.get("url") or "") + (i.get("title") or "") not in seen)

    if not items:
        return {"ok": False, "skipped": "检索无结果", "errors": errors[:3], "candidates": []}

    items = _relevant(items, raw)
    if not items:
        return {"ok": False, "skipped": "检索结果与该品类无关，未收录", "errors": errors[:3], "candidates": []}

    codes = _extract_codes(items)
    evidence_text = " ".join(
        f"{i.get('title', '')} {i.get('snippet', '')}" for i in items[:3]
    )
    name = raw
    candidate = {
        "id": _cand_id(name),
        "name": name,
        "keywords": list(dict.fromkeys([name, *(i for i in _tokens(name))]))[:6],
        "hs": codes[0][0] if codes else "",
        "hs_options": [c for c, _ in codes[1:]],
        "duty": {},
        "certs": _certs(evidence_text),
        "risk": _risk(evidence_text),
        "dg": _is_dg(evidence_text),
        "notes": (
            "自动收录，待人工核对 HS 与目的国税率；来源见 evidence。"
            if codes
            else "仅检索到公开资讯，未解析出 HS 编码，需人工补充。"
        ),
        "status": "pending",
        "confidence": "low" if len(items) < 2 else "medium",
        "observed_count": len(items),
        "queries": queries,
        "evidence": [
            {
                "title": (i.get("title") or "")[:80],
                "url": i.get("url"),
                "published": i.get("published"),
                "engine": i.get("engine"),
            }
            for i in items[:3]
        ],
        "added_at": _now(),
        "updated_at": _now(),
        "checked_at": _now(),
        "confirmed_at": None,
        "miss_streak": 0,
        "trigger": trigger,
    }

    doc = _doc()
    existing = next((c for c in doc["items"] if c.get("id") == candidate["id"] and c.get("status") == "pending"), None)
    if existing:
        existing.update(
            {
                "hs": candidate["hs"] or existing.get("hs", ""),
                "hs_options": candidate["hs_options"] or existing.get("hs_options", []),
                "evidence": (existing.get("evidence", []) + candidate["evidence"])[:4],
                "observed_count": int(existing.get("observed_count", 0)) + len(items),
                "confidence": candidate["confidence"],
                "updated_at": _now(),
                "checked_at": _now(),
            }
        )
        saved = existing
    else:
        doc["items"].append(candidate)
        saved = candidate

    # 已确认条目里已有同一 HS：不再重复登记
    same_hs = [x for x in confirmed_items() if saved.get("hs") and x.get("hs") == saved.get("hs")]
    if same_hs:
        saved["status"] = "duplicate"
        saved["notes"] = f"与已确认条目「{same_hs[0].get('name')}」同一 HS，已并入其关键词"
        with file_lock(HS_FILE):
            payload = _confirmed_doc()
            for item in payload.get("auto_items", []):
                if item.get("hs") == saved.get("hs") and not item.get("disabled"):
                    kws = list(dict.fromkeys(list(item.get("keywords", [])) + [raw]))
                    item["keywords"] = kws[:8]
            _write(HS_FILE, payload)
        _clear_cache()

    _save(doc)

    from logistics_agent.core.telemetry import live

    live.log("kb_ingest", keyword=name, hs=saved.get("hs"), trigger=trigger, status=saved.get("status"))
    return {
        "ok": True,
        "cached": False,
        "status": saved.get("status"),
        "candidates": [candidate_brief(saved)],
        "errors": errors[:3],
    }


def _tokens(text: str) -> list[str]:
    """只取英文/数字词做别名：中文靠整名做子串匹配，滑窗切词会带进「物生」这类噪声。"""
    return [x.lower() for x in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{1,}", str(text or ""))][:6]


def _age_hours(stamp: str | None) -> float:
    try:
        t = time.strptime(str(stamp), "%Y-%m-%d %H:%M:%S")
        return (time.time() - time.mktime(t)) / 3600
    except (TypeError, ValueError):
        return 10 ** 6


# ------------------------- 周更落盘 -------------------------

def apply_weekly(categories: dict) -> dict:
    """把周报里的品类复核结论落盘（调用前由 kb_sync 备份）。

    - verify：刷新 last_verified_at / 证据、miss_streak 归零；
    - hs_changes：按周报结论改 HS 并附证据（低置信，已在周报里人工确认过）；
    - missing：连续 3 周无证据的条目停用（保留数据，不再参与匹配）；
    - prune_pending：过期且无证据的待审候选删除。
    """
    stats = {"verified": 0, "hs_changed": 0, "disabled": 0, "pruned": 0, "unchanged": 0}
    payload = _confirmed_doc()
    auto = payload.get("auto_items", [])

    for row in categories.get("verify", []):
        hit = next((x for x in auto if x.get("hs") == row.get("hs") and not x.get("disabled")), None)
        if not hit:
            stats["unchanged"] += 1
            continue
        hit["last_verified_at"] = _now()
        hit["checked_at"] = _now()
        hit["miss_streak"] = 0
        if row.get("evidence"):
            hit["evidence"] = (hit.get("evidence") or [])[:2] + row["evidence"][:2]
            hit["evidence"] = hit["evidence"][:4]
        stats["verified"] += 1

    for row in categories.get("hs_changes", []):
        hit = next((x for x in auto if x.get("hs") == row.get("current") and not x.get("disabled")), None)
        if not hit:
            stats["unchanged"] += 1
            continue
        hit["hs_previous"] = row.get("current")
        hit["hs"] = row.get("proposed")
        hit["checked_at"] = _now()
        hit["last_verified_at"] = _now()
        hit["miss_streak"] = 0
        hit["notes"] = (hit.get("notes") or "") + "｜周更复核：HS 依据当周公开资讯调整，需关注税则差异"
        if row.get("evidence"):
            hit["evidence"] = ((hit.get("evidence") or []) + row["evidence"][:2])[-4:]
        stats["hs_changed"] += 1

    for row in categories.get("missing", []):
        hit = next((x for x in auto if x.get("hs") == row.get("hs") and not x.get("disabled")), None)
        if not hit:
            stats["unchanged"] += 1
            continue
        streak = int(hit.get("miss_streak", 0)) + 1
        hit["miss_streak"] = streak
        hit["checked_at"] = _now()
        if streak >= 3:
            hit["disabled"] = True
            hit["disabled_at"] = _now()
            hit["disabled_reason"] = "连续 3 周复核无公开证据，停用（数据保留，可在周报或配置页恢复）"
            stats["disabled"] += 1
        else:
            stats["verified"] += 1

    if any(row.get("id") for row in categories.get("prune_pending", [])):
        doc = _doc()
        ids = {row["id"] for row in categories.get("prune_pending", [])}
        before = len(doc.get("items", []))
        doc["items"] = [c for c in doc.get("items", []) if not (c.get("id") in ids and c.get("status") == "pending")]
        stats["pruned"] = before - len(doc["items"])
        _save(doc)

    if any(stats[k] for k in ("verified", "hs_changed", "disabled")):
        payload.setdefault("_meta", {})
        payload["_meta"]["auto_updated_at"] = _now()
        with file_lock(HS_FILE):
            _write(HS_FILE, payload)

    _clear_cache()
    return stats


def prune_orphans(ttl_days: int | None = None) -> int:
    """兜底清理：超过 TTL 仍无证据、且没进周报的 pending 候选直接删（默认不启用）。"""
    ttl = KB_CANDIDATE_TTL_DAYS if ttl_days is None else int(ttl_days)
    doc = _doc()
    expired = [
        c
        for c in doc.get("items", [])
        if c.get("status") == "pending"
        and _age_hours(c.get("added_at")) > ttl * 24
        and int(c.get("observed_count", 0)) <= 1
    ]
    if not expired:
        return 0
    ids = {c.get("id") for c in expired}
    doc["items"] = [c for c in doc.get("items", []) if c.get("id") not in ids]
    _save(doc)
    return len(expired)


# ------------------------- 人工确认 / 忽略 -------------------------

def list_candidates(status: str = "pending", limit: int = 50) -> list[dict]:
    rows = _doc().get("items", [])
    if status and status != "all":
        rows = [c for c in rows if c.get("status") == status]
    rows = sorted(rows, key=lambda c: c.get("updated_at") or "", reverse=True)
    return [candidate_brief(c) for c in rows[: max(1, min(int(limit), 200))]]


def review(selector: str, action: str = "confirm", note: str = "", hs: str = "") -> dict:
    """确认 → 写入 hs_codes.json 的 auto_items（与基线同结构）；忽略 → 留在候选表标 rejected。

    hs 不为空时以人工填写的编码为准（候选自动解析的编码可能不准）。
    """
    sel = str(selector or "").strip()
    if not sel:
        return {"ok": False, "error": "缺少候选 id / HS / 品名"}
    if action not in ("confirm", "reject"):
        return {"ok": False, "error": "action 只能是 confirm 或 reject"}

    doc = _doc()
    target = None
    for c in doc["items"]:
        if c.get("status") != "pending":
            continue
        if sel in (c.get("id"), c.get("hs"), c.get("name")) or _hit(c, sel):
            target = c
            break
    if not target:
        return {"ok": False, "error": f"未找到待审候选：{sel}"}

    if action == "confirm" and hs.strip():
        manual = re.sub(r"[^0-9]", "", hs.strip())[:10]
        if not (6 <= len(manual) <= 10):
            return {"ok": False, "error": "HS 编码需为 6-10 位数字"}
        target["hs"] = manual
        target["hs_manual"] = True

    if action == "reject":
        target["status"] = "rejected"
        target["updated_at"] = _now()
        target["review_note"] = note
        _save(doc)
        return {"ok": True, "action": "reject", "id": target.get("id"), "name": target.get("name")}

    if not target.get("hs"):
        return {"ok": False, "error": "该候选没有解析出 HS 编码，请先人工补充 HS 再确认"}

    payload = _confirmed_doc()
    auto = payload.setdefault("auto_items", [])
    if any(x.get("hs") == target["hs"] and not x.get("disabled") for x in auto):
        return {"ok": False, "error": f"HS {target['hs']} 已在知识库中，无需重复确认"}

    entry = {
        "hs": target["hs"],
        "name": target.get("name"),
        "keywords": list(dict.fromkeys(target.get("keywords", [])))[:8],
        "duty": target.get("duty") or {},
        "certs": target.get("certs", []),
        "risk": target.get("risk", "medium"),
        "dg": bool(target.get("dg")),
        "notes": (target.get("notes") or "") + (f"｜复核备注：{note}" if note else ""),
        "source": "auto",
        "confidence": target.get("confidence", "low"),
        "confirmed_at": _now(),
        "evidence": target.get("evidence", []),
    }
    payload.setdefault("_meta", {})
    payload["_meta"].update(
        {
            "note": "演示用 HS 归类与税率基线，仅覆盖常见出口品类。税率按目的国简化，实际以官方税则为准。"
            "auto_items 为自动收录并经人工确认的品类，带来源字段。",
            "auto_updated_at": _now(),
        }
    )
    auto.append(entry)
    with file_lock(HS_FILE):
        _write(HS_FILE, payload)

    doc["items"].remove(target)
    doc.setdefault("confirmed", []).append(
        {"id": target.get("id"), "name": target.get("name"), "hs": target["hs"], "confirmed_at": _now(), "note": note}
    )
    _save(doc)
    _clear_cache()

    from logistics_agent.core.telemetry import live

    live.log("kb_confirm", name=target.get("name"), hs=target["hs"], note=note)
    return {"ok": True, "action": "confirm", "hs": target["hs"], "name": target.get("name"), "added": entry}


def _clear_cache() -> None:
    try:
        from logistics_agent.tools.knowledge import _load

        _load.cache_clear()
    except Exception:  # noqa: BLE001
        pass


__all__ = ["ingest", "find_pending", "find_rejected", "confirmed_items", "list_candidates", "review", "candidate_brief"]
