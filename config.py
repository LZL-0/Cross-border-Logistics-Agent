# -*- coding: utf-8 -*-
"""全局配置槽：模型、预算、权限、路径根与业务基线参数。

约定：
- 所有路径以项目根为基准，不写盘符与绝对路径；
- 敏感信息只从 .env 读取，代码不写死 Key；
- 本模块是全项目唯一配置入口，其它模块只从这里取常量。
"""

import os
from pathlib import Path

try:  # 未安装 python-dotenv 时不影响导入
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:  # noqa: BLE001
    pass


def _env_str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(str(os.getenv(key, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(str(os.getenv(key, default)).strip())
    except (TypeError, ValueError):
        return default


# ------------------------- 路径根 -------------------------

ROOT = Path(__file__).resolve().parent

_DIR_KEYS = ("data", "knowledge", "memory", "telemetry", "quotes", "uploads", "dist")

PATHS = {
    # 目录
    "data": ROOT / "data",
    "knowledge": ROOT / "data" / "knowledge",
    "memory": ROOT / "data" / "memory",
    "telemetry": ROOT / "data" / "telemetry",
    "quotes": ROOT / "data" / "quotes",
    "uploads": ROOT / "data" / "uploads",
    "dist": ROOT / "dist",
    "ui": ROOT / "ui",
    # 知识库
    "countries": ROOT / "data" / "knowledge" / "countries.json",
    "hs_codes": ROOT / "data" / "knowledge" / "hs_codes.json",
    "carriers": ROOT / "data" / "knowledge" / "carriers.json",
    "ports": ROOT / "data" / "knowledge" / "ports.json",
    # 运行数据
    "short_term": ROOT / "data" / "memory" / "short_term.jsonl",
    "long_term": ROOT / "data" / "memory" / "long_term.jsonl",
    "telemetry_file": ROOT / "data" / "telemetry" / "live.jsonl",
    "quote_log": ROOT / "data" / "quotes" / "quote_log.csv",
}

for _key in _DIR_KEYS:
    PATHS[_key].mkdir(parents=True, exist_ok=True)

# ------------------------- 模型 -------------------------

LLM_API_KEY = _env_str("LLM_API_KEY")
LLM_BASE_URL = _env_str("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = _env_str("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 60)

# ------------------------- 权限与约束 -------------------------

# plan：写类工具需 confirm=true；auto：免确认；readonly：只读
AGENT_PERMISSION_MODE = _env_str("AGENT_PERMISSION_MODE", "plan").lower()
# allowlist：仅放行白名单域名；block：禁止外网（本项目默认离线，无外网工具）
TOOL_NET_POLICY = _env_str("TOOL_NET_POLICY", "allowlist").lower()
TOOL_NET_ALLOWLIST = tuple(
    x.strip().lower() for x in _env_str("TOOL_NET_ALLOWLIST").split(",") if x.strip()
)
TOOL_PATH_ROOTS = tuple(
    Path(item).resolve()
    for item in (p.strip() for p in _env_str("TOOL_PATH_ROOTS").split(os.pathsep) if p.strip())
) or (ROOT,)

MAX_TOOL_CALLS = _env_int("MAX_TOOL_CALLS", 12)
REACT_MAX_ROUNDS = _env_int("REACT_MAX_ROUNDS", 4)
CONTEXT_CHAR_BUDGET = _env_int("CONTEXT_CHAR_BUDGET", 6000)

# ------------------------- 业务基线 -------------------------

# 旺季月份（叠加旺季附加费与清关延误）
PEAK_MONTHS = tuple(
    int(x) for x in _env_str("PEAK_MONTHS", "9,10,11,12").split(",") if x.strip().isdigit()
)
# 默认尾程派送天数（按运输方式）
TAIL_DAYS = {"express": 1.0, "air": 2.0, "ocean": 3.0, "lcl": 3.0, "fcl": 3.0, "rail": 2.5}
# 承运商不自营清关时，比价中计入的代理清关预估（元），保证总成本可比
AGENCY_CLEARANCE_FEE = _env_int("AGENCY_CLEARANCE_FEE", 300)
# 计费货币与汇率哨兵（演示环境不做换算，统一人民币）
CURRENCY = "CNY"
# 报价单导出目录
QUOTE_DIR = PATHS["quotes"]
