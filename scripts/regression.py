# -*- coding: utf-8 -*-
"""回归测试：运输方式归一化 / CSV 表头自愈 / 入参边界 / 跨源写保护 / 基线数值。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\regression.py

会在本机随机端口临时起一份服务做 HTTP 校验，结束后自动关闭；
CSV 相关用例在临时目录执行，不碰 data/quotes 的真实数据。
"""

import csv
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED, FAILED = [], []


def check(name, fn):
    try:
        detail = fn()
        PASSED.append(name)
        print(f"[PASS] {name}" + (f" -> {detail}" if detail else ""))
    except Exception as exc:  # noqa: BLE001
        FAILED.append((name, exc))
        print(f"[FAIL] {name} -> {type(exc).__name__}: {exc}")


def assert_close(actual, expected, tol=0.01, label=""):
    if abs(float(actual) - float(expected)) > tol:
        raise AssertionError(f"{label} 期望 {expected}，实际 {actual}")


# ---------------- 进程内：引擎口径 ----------------

def test_mode_mapping():
    from logistics_agent.tools.clearance import normalize_mode

    cases = {
        "express": "express", "国际快递": "express", "空运": "air", "海运": "ocean",
        "拼柜": "lcl", "海运拼柜": "lcl", "海运整柜": "fcl", "铁路": "rail", "LCL": "lcl",
    }
    for raw, expected in cases.items():
        got = normalize_mode(raw)
        if got != expected:
            raise AssertionError(f"normalize_mode({raw!r}) = {got!r}，期望 {expected!r}")
    if normalize_mode("随便") is not None:
        raise AssertionError("未知运输方式应返回 None")
    return f"{len(cases)} 组别名"


def test_unknown_mode_fails_fast():
    from logistics_agent.tools.rates import compare_rates

    try:
        compare_rates("德国", 20, mode="随便")
    except ValueError as exc:
        if "未知运输方式" not in str(exc):
            raise AssertionError(f"报错信息缺少提示：{exc}")
        return "明确报错"
    raise AssertionError("未知运输方式未报错")


def test_baseline_quote():
    from logistics_agent.tools.rates import compare_rates

    data = compare_rates("德国", weight_kg=20, length=50, width=40, height=30,
                         hs="6109100010", declared_value=5000, pieces=1)
    if data["count"] != 8:
        raise AssertionError(f"可选承运商 {data['count']}，期望 8")
    assert_close(data["tax"]["total_tax"], 1664, label="税费")
    rec = data["recommendation"]
    if rec["carrier"] != "顺丰国际":
        raise AssertionError(f"推荐 {rec['carrier']}，期望 顺丰国际")
    assert_close(rec["landed_total"], 3479.91, label="总成本")
    if [round(x, 1) for x in rec["eta_days"]] != [9.4, 14.2]:
        raise AssertionError(f"时效 {rec['eta_days']}")
    return "8 家 / 税 1664 / 顺丰 3479.91 / 9.4-14.2 天"


def test_mode_display_name_quote():
    from logistics_agent.tools.rates import compare_rates
    from logistics_agent.tools.clearance import estimate

    data = compare_rates("德国", weight_kg=300, length=120, width=80, height=60,
                         mode="海运拼柜", pieces=10, hs="9403600000", declared_value=30000)
    if data["count"] != 1:
        raise AssertionError(f"海运拼柜可选 {data['count']} 家，期望 1")
    assert_close(data["recommendation"]["landed_total"], 10562.9, label="拼柜总成本")
    days = estimate("德国", mode="海运拼柜", hs="9403600000")["clearance_days"]
    if [round(x, 1) for x in days] != [7.5, 10.0]:
        raise AssertionError(f"拼柜清关 {days}，期望 [7.5, 10.0]")
    return "德迅 10562.9 / 清关 7.5-10 天"


def test_csv_header_self_heal():
    from logistics_agent.tools.catalog import QUOTE_FIELDS, append_rows, count_rows, read_csv

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "quote_log.csv"
        path.write_bytes(b"\xef\xbb\xbf")  # 只剩 BOM，历史坏文件
        rows = read_csv(path, QUOTE_FIELDS)
        if rows:
            raise AssertionError(f"坏文件不应读出数据，实际 {rows}")
        first = path.read_text(encoding="utf-8-sig").splitlines()[0]
        if first.strip() != ",".join(QUOTE_FIELDS):
            raise AssertionError(f"读取时未补表头：{first[:40]}")

        append_rows(path, [{"quote_id": "q1", "carrier": "顺丰国际", "mode": "express"}], QUOTE_FIELDS)
        append_rows(path, [{"quote_id": "q2", "carrier": "德迅", "mode": "lcl"}], QUOTE_FIELDS)
        rows = read_csv(path, QUOTE_FIELDS)
        if len(rows) != 2 or rows[0]["quote_id"] != "q1" or rows[1]["quote_id"] != "q2":
            raise AssertionError(f"读回异常：{rows}")
        if count_rows(path) != 2:
            raise AssertionError(f"count_rows = {count_rows(path)}")
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if sum(1 for line in lines if line.startswith("quote_id")) != 1:
            raise AssertionError("表头出现多次")
        return "BOM-only → 自动补表头，追加 2 条读回正常"


def test_csv_formula_escape():
    from logistics_agent.tools.catalog import QUOTE_FIELDS, append_rows, read_csv

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "q.csv"
        bad = '=HYPERLINK("http://x","点我")'
        append_rows(path, [{"quote_id": "q", "carrier": bad}], QUOTE_FIELDS)
        rows = read_csv(path, QUOTE_FIELDS)
        if not rows[0]["carrier"].startswith("'="):
            raise AssertionError(f"未转义公式：{rows[0]['carrier']}")
        return "已加 ' 前缀"


# ---------------- 进程内：入参校验 ----------------

def test_pydantic_bounds():
    from pydantic import ValidationError
    from server import QuoteIn

    bad_cases = [
        {"country": "德国", "weight_kg": -20},
        {"country": "德国", "weight_kg": 0},
        {"country": "德国", "pieces": 0},
        {"country": "德国", "declared_value": -100},
        {"country": "", "weight_kg": 5},
        {"country": "德国", "weight_kg": 5, "length": -1},
    ]
    for payload in bad_cases:
        try:
            QuoteIn(**payload)
        except ValidationError:
            continue
        raise AssertionError(f"非法入参未被拦截：{payload}")
    ok = QuoteIn(country="德国", weight_kg=20, pieces=1)
    if ok.weight_kg != 20:
        raise AssertionError("合法入参被误伤")
    return f"{len(bad_cases)} 组非法入参全部拦截"


# ---------------- HTTP：路由 / CORS ----------------

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")


def wait_ready(base, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            status, _, _ = http("GET", base + "/api/stats", timeout=3)
            if status == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise RuntimeError("临时服务启动超时")


def main():
    check("运输方式归一化", test_mode_mapping)
    check("未知运输方式快速失败", test_unknown_mode_fails_fast)
    check("基线比价", test_baseline_quote)
    check("显示名比价/清关", test_mode_display_name_quote)
    check("CSV 表头自愈", test_csv_header_self_heal)
    check("CSV 公式转义", test_csv_formula_escape)
    check("入参边界校验", test_pydantic_bounds)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env_file = ROOT / ".env"
    before = env_file.read_bytes() if env_file.exists() else b""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_ready(base)

        def http_negative():
            cases = [
                ("负重量", {"country": "德国", "weight_kg": -20}),
                ("零重量", {"country": "德国", "weight_kg": 0}),
                ("负件数", {"country": "美国", "weight_kg": 5, "pieces": -3}),
                ("负货值", {"country": "美国", "weight_kg": 5, "declared_value": -100}),
            ]
            for label, body in cases:
                status, _, raw = http("POST", base + "/api/quote", body)
                if status != 422:
                    raise AssertionError(f"{label} 返回 {status}，期望 422：{raw[:120]}")
            return f"{len(cases)} 组均 422"

        def http_mode():
            status, _, raw = http("POST", base + "/api/quote",
                                  {"country": "德国", "weight_kg": 300, "length": 120, "width": 80,
                                   "height": 60, "pieces": 10, "hs": "9403600000",
                                   "declared_value": 30000, "mode": "海运拼柜"})
            data = json.loads(raw)
            if status != 200 or not data.get("ok") or data["data"]["count"] != 1:
                raise AssertionError(f"海运拼柜 HTTP 失败：{status} {raw[:160]}")
            assert_close(data["data"]["recommendation"]["landed_total"], 10562.9, label="拼柜")
            return "count=1 / 10562.9"

        def http_unknown_mode():
            status, _, raw = http("POST", base + "/api/quote",
                                  {"country": "德国", "weight_kg": 20, "mode": "随便"})
            data = json.loads(raw)
            if status != 200 or data.get("ok") is not False or "未知运输方式" not in data.get("error", ""):
                raise AssertionError(f"未知方式未明确报错：{status} {raw[:160]}")
            return "ok=false + 可选值提示"

        def http_cors_preflight():
            status, headers, _ = http("OPTIONS", base + "/api/config", {}, headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            })
            allow = headers.get("access-control-allow-origin")
            if status == 200 or allow:
                raise AssertionError(f"跨源预检被放行：{status} allow-origin={allow}")
            return f"预检被拒（{status}）"

        def http_cross_origin_write():
            status, _, raw = http("POST", base + "/api/config",
                                  {"values": {"LLM_BASE_URL": "https://evil.example"}},
                                  headers={"Origin": "https://evil.example"})
            if status != 403:
                raise AssertionError(f"跨源写配置返回 {status}：{raw[:120]}")
            now = env_file.read_bytes() if env_file.exists() else b""
            if now != before:
                raise AssertionError(".env 被跨源请求改写")
            return "403 且 .env 未变"

        def http_same_origin_ok():
            status, _, raw = http("POST", base + "/api/quote",
                                  {"country": "美国", "weight_kg": 8}, headers={"Origin": base})
            if status != 200:
                raise AssertionError(f"同源 POST 被误拦：{status} {raw[:120]}")
            return "同源写/算请求正常"

        check("HTTP 负值拦截", http_negative)
        check("HTTP 显示名比价", http_mode)
        check("HTTP 未知方式报错", http_unknown_mode)
        check("CORS 预检收紧", http_cors_preflight)
        check("跨源写配置拦截", http_cross_origin_write)
        check("同源请求放行", http_same_origin_ok)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    total = len(PASSED) + len(FAILED)
    print(f"\n=== {len(PASSED)}/{total} 通过 ===")
    if FAILED:
        for name, exc in FAILED:
            print(f"  失败：{name} -> {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
