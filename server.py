# -*- coding: utf-8 -*-
"""FastAPI 入口：/api/ask 等路由 + 托管 ui/ 静态资源。

启动：
    uvicorn server:app --host 127.0.0.1 --port 8000
"""

import json
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import (
    AGENT_PERMISSION_MODE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    MAX_TOOL_CALLS,
    PATHS,
    ROOT,
    TOOL_NET_ALLOWLIST,
    TOOL_NET_POLICY,
)
from logistics_agent.Agents import Supervisor
from logistics_agent.core.constraint.layer import check_tool_call
from logistics_agent.core.dispatch.orchestrator import Orchestrator
from logistics_agent.core.telemetry import live
from logistics_agent.tools.catalog import quote_rows
from logistics_agent.tools.clearance import estimate_clearance, port_status
from logistics_agent.tools.knowledge import lookup_country, match_hs_candidates
from logistics_agent.tools.rates import compare_rates, list_carriers
from logistics_agent.tools.shipment import export_quote, plan_shipment

VERSION = "20261121"

app = FastAPI(
    title="跨境物流引擎",
    description="海关数据 + 承运商资源：清关时效预估与多承运商比价",
    version=VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许 file:// / IDE 预览等跨源访问调试接口（不带 credentials）
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/ui", StaticFiles(directory=str(PATHS["ui"])), name="ui")

# 调试平面：仅 DEBUG_TOOLKIT=1 时挂载，正式包不加载
from debug_kit.flags import DEBUG as _DEBUG  # noqa: E402

if _DEBUG:
    from debug_kit.router import router as _debug_router

    import debug_kit.tool  # noqa: F401  导入即注册 debugger 角色工具

    app.include_router(_debug_router)

    _DEBUG_HTML = Path(__file__).parent / "debug_kit" / "ui" / "debug.html"

    def _debug_page():
        """禁止缓存，避免浏览器一直拿着旧版调试页。"""
        return FileResponse(
            _DEBUG_HTML,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    app.get("/debug", include_in_schema=False)(lambda: _debug_page())
    app.get("/debug/debug.html", include_in_schema=False)(lambda: _debug_page())

    app.mount(
        "/debug",
        StaticFiles(directory=str(Path(__file__).parent / "debug_kit" / "ui")),
        name="debug",
    )

SUPERVISOR = Supervisor()
ORCHESTRATOR = Orchestrator(SUPERVISOR)


class AskIn(BaseModel):
    text: str
    session_id: str | None = None


class QuoteIn(BaseModel):
    country: str
    weight_kg: float = 1
    length: float = 0
    width: float = 0
    height: float = 0
    mode: str = ""
    pieces: int = 1
    hs: str = ""
    declared_value: float = 0
    docs_provided: list[str] | None = None
    prefer: str = "balance"


class ClearanceIn(BaseModel):
    country: str
    mode: str = "express"
    hs: str = ""
    docs_provided: list[str] | None = None
    port: str = ""
    declared_value: float = 0


class PlanIn(QuoteIn):
    pass


class ExportIn(BaseModel):
    country: str
    carrier: str
    mode: str = ""
    weight_kg: float = 1
    declared_value: float = 0
    freight_total: float = 0
    tax: float = 0
    landed_total: float = 0
    eta_date: str = ""
    note: str = ""


def _guard(fn, *args, **kwargs):
    """统一兜底：工具异常转成结构化错误，不让 500 直接打到前端。"""
    try:
        return {"ok": True, "data": fn(*args, **kwargs)}
    except Exception as exc:  # noqa: BLE001
        live.log("api_error", fn=getattr(fn, "__name__", str(fn)), error=str(exc)[:300])
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/ask")
def ask(payload: AskIn) -> dict:
    """一次问答：Supervisor 规则路由 → 业务智能体 → 统一结构。"""
    if not (payload.text or "").strip():
        return {"answer": "请输入内容。", "trace": [], "agent": "supervisor"}
    result = ORCHESTRATOR.handle(payload.text, payload.session_id)
    if _DEBUG:  # 调试平面内存记录，未开启时零开销
        from debug_kit import recorder

        recorder.record(
            "ask",
            session_id=payload.session_id or "",
            agent=result.get("agent", ""),
            text=payload.text[:60],
        )
    return result


@app.post("/api/quote")
def api_quote(payload: QuoteIn) -> dict:
    """结构化比价（供询价表单直接调用）。"""
    return _guard(compare_rates, **payload.model_dump())


@app.post("/api/plan")
def api_plan(payload: PlanIn) -> dict:
    """结构化全链路方案。"""
    return _guard(plan_shipment, **payload.model_dump())


@app.post("/api/clearance")
def api_clearance(payload: ClearanceIn) -> dict:
    """结构化清关时效预估。"""
    return _guard(estimate_clearance, **payload.model_dump())


@app.post("/api/export")
def api_export(payload: ExportIn) -> dict:
    """导出报价单：同样走约束层（危险写 + confirm）。"""
    args = {**payload.model_dump(), "confirm": True}
    ok, reason = check_tool_call("shipment.export_quote", args)
    if not ok:
        return {"ok": False, "error": reason}
    return _guard(export_quote, **args)


@app.get("/api/carriers")
def api_carriers(country: str = "", mode: str = "") -> dict:
    return _guard(list_carriers, country=country, mode=mode)


@app.get("/api/country")
def api_country(country: str) -> dict:
    return _guard(lookup_country, country=country)


@app.get("/api/ports")
def api_ports(port: str = "") -> dict:
    return _guard(port_status, port=port)


@app.get("/api/hs")
def api_hs(keyword: str = "", hs: str = "", limit: int = 5) -> dict:
    return {"ok": True, "data": {"found": bool(match_hs_candidates(keyword, hs, limit)),
                                 "candidates": match_hs_candidates(keyword, hs, limit)}}


@app.get("/api/duty")
def api_duty(hs: str = "", country: str = "", declared_value: float = 0, freight: float = 0) -> dict:
    """指定 HS + 目的国的税费测算（供合规页使用）。"""
    from logistics_agent.tools.knowledge import duty_calc

    return _guard(duty_calc, hs=hs, country=country, declared_value=declared_value, freight=freight)


@app.get("/api/stats")
def api_stats() -> dict:
    data = SUPERVISOR.stats()
    data["quotes"]["recent"] = quote_rows()[-5:]
    return data


@app.get("/api/quotes")
def api_quotes(limit: int = 20) -> dict:
    rows = quote_rows()
    return {"ok": True, "data": {"total": len(rows), "rows": rows[-max(1, min(limit, 200)):]}}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    """上传 csv/txt/md 到 data/uploads（只保留文件名，防目录穿越）。"""
    name = Path(file.filename or "upload.bin").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".txt", ".md"}:
        return {"ok": False, "error": "仅支持 csv / txt / md"}
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return {"ok": False, "error": "文件超过 5MB 限制"}
    target = PATHS["uploads"] / name
    target.write_bytes(content)
    live.log("upload", filename=name, size=len(content))
    return {"ok": True, "filename": name, "size": len(content)}


# ------------------------- 配置读写 -------------------------

ENV_WRITABLE = (
    "LLM_MODEL",
    "LLM_BASE_URL",
    "AGENT_PERMISSION_MODE",
    "TOOL_NET_POLICY",
    "TOOL_NET_ALLOWLIST",
    "MAX_TOOL_CALLS",
)


class ConfigIn(BaseModel):
    values: dict[str, str] = {}


@app.get("/api/config")
def read_config() -> dict:
    """返回可写配置（API Key 只报是否已配置，不回传明文）。"""
    return {
        "env": {
            "LLM_MODEL": LLM_MODEL,
            "LLM_BASE_URL": LLM_BASE_URL,
            "LLM_API_KEY_SET": bool(LLM_API_KEY),
            "AGENT_PERMISSION_MODE": AGENT_PERMISSION_MODE,
            "TOOL_NET_POLICY": TOOL_NET_POLICY,
            "TOOL_NET_ALLOWLIST": ",".join(TOOL_NET_ALLOWLIST),
            "MAX_TOOL_CALLS": str(MAX_TOOL_CALLS),
        },
        "writable": list(ENV_WRITABLE),
        "need_restart": True,
    }


@app.post("/api/config")
def save_config(payload: ConfigIn) -> dict:
    """白名单内的配置写回 .env；不热更新，需重启服务。"""
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated, added = [], []

    for key, raw in (payload.values or {}).items():
        key = str(key).strip().upper()
        if key not in ENV_WRITABLE:
            continue
        value = str(raw).strip().replace("\n", "").replace("\r", "")
        index = next(
            (i for i, line in enumerate(lines) if "=" in line and line.split("=", 1)[0].strip() == key),
            None,
        )
        if index is None:
            lines.append(f"{key}={value}")
            added.append(key)
        elif lines[index].split("=", 1)[1].strip() != value:
            lines[index] = f"{key}={value}"
            updated.append(key)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    live.log("config_save", updated=updated, added=added)
    return {"ok": True, "updated": updated, "added": added, "need_restart": True}


@app.get("/api/debug/tail")
def debug_tail(n: int = 30) -> dict:
    """读取遥测日志最后 n 条，供调试面板展示。"""
    path = PATHS["telemetry_file"]
    if not path.exists():
        return {"events": []}
    count = max(1, min(int(n), 200))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"events": events}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{V}}", VERSION))


if __name__ == "__main__":  # 手动运行时使用；正常请用 uvicorn server:app
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
