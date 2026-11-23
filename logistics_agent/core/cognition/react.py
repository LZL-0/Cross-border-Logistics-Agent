# -*- coding: utf-8 -*-
"""认知层：简易 ReAct 循环。

模型每轮只输出一个 JSON：
- {"action": "工具名", "args": {...}} → 执行工具，把观察结果回填
- {"final": "最终回答"}              → 结束
最多 REACT_MAX_ROUNDS 轮，超出返回已有观察。
"""

import inspect
import json
import re
import time

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    REACT_MAX_ROUNDS,
    REALTIME_ENABLED,
)

_JSON_RE = re.compile(r"\{.*\}", re.S)

_SYSTEM_TMPL = """你是「跨境物流引擎」的智能体，角色：{role}。
只做跨境运输方案、清关时效、税费与比价相关的分析与计算，不编造运价与税率。
今天是 {today}（北京时间），涉及「最新/当前/今年」的问题以这个日期为基准。

可用工具（名称(参数)：说明）：
{tools}

参数名必须与括号里的完全一致，不要自创字段；可选参数拿不准就省略，用默认值。
{routing}
输出格式（严格遵守，只输出一个 JSON，不要 Markdown 代码块）：
- 需要调用工具：{{"action": "工具名", "args": {{参数对象}}}}
- 可以结束：{{"final": "给用户的中文回答，含关键数字与假设"}}
一次只调用一个工具；拿到观察结果后再决定下一步。最多 {rounds} 轮，轮次有限，先用工具取数再作答。
"""

_REALTIME_HINT = """【实时信息规则】
你手上有联网工具，凡是下面的问题都必须先取数再回答，不许凭记忆给数：
- 运价、燃油附加费、旺季附加费、船公司涨价通知
- 港口拥堵、船期/舱位、航班运力
- 关税税率、贸易政策、认证法规的变动
- 汇率、燃油价格
取数顺序：先 realtime.web_search 拿标题+摘要，必要时 realtime.fetch_page 读原文核实，
海运整体行情用 realtime.scfi_index（上海航运交易所 SCFI 综合指数），
货币换算用 realtime.fx_rate。检索词用中文短关键词（3–5 段，如「美国 清关 关税」），不要整句英文或长句；查不到就换个更短的说法再试一次。
回答里写清「数据时间 + 来源」，例：
「据 xxx（2026-11-23 检索）：…」。工具失败就说明取数失败，并退回知识库基线、标明是基线。
"""

_OFFLINE_HINT = "【当前离线】没有联网工具。所有运价/税率/汇率都来自知识库基线，回答里必须标明「非实时，演示基线」。"


def _parse_json(text: str) -> dict | None:
    """从模型输出里抠出第一个 JSON 对象。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _sig_params(fn) -> str:
    """把函数签名压成 "a: str, b: int = 5" 形式，避免模型自创参数名。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return ""
    parts = []
    for param in sig.parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        label = param.name
        ann = param.annotation
        if ann is not inspect.Parameter.empty:
            label += f": {ann.__name__}" if isinstance(ann, type) else f": {str(ann).replace('typing.', '')}"
        if param.default is not inspect.Parameter.empty:
            default = param.default
            label += f" = {default!r}" if isinstance(default, str) else f" = {default}"
        parts.append(label)
    return ", ".join(parts)


def _tool_doc(tools: dict[str, dict]) -> str:
    lines = []
    for name, item in tools.items():
        params = _sig_params(item.get("func"))
        lines.append(f"- {name}({params})：{item['description']}")
    return "\n".join(lines) or "（当前角色没有可用工具）"


def run(
    role: str,
    user_text: str,
    tools: dict[str, dict],
    execute,
    context_text: str = "",
    system_extra: str = "",
) -> dict:
    """执行 ReAct 循环；execute(name, args) 返回 (ok, payload)。"""
    from openai import OpenAI  # 延迟导入：未装 openai 只影响此路径

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT)
    tool_doc = _tool_doc(tools)

    has_realtime = REALTIME_ENABLED and any(name.startswith("realtime.") for name in tools)
    routing = _REALTIME_HINT if has_realtime else _OFFLINE_HINT

    system = _SYSTEM_TMPL.format(
        role=role,
        tools=tool_doc,
        rounds=REACT_MAX_ROUNDS,
        today=time.strftime("%Y-%m-%d", time.localtime()),
        routing=routing,
    )
    if system_extra:
        system += "\n" + system_extra
    if context_text:
        system += "\n\n【上下文】\n" + context_text

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    trace: list[dict] = []
    observations: list[str] = []

    for round_no in range(1, max(1, REACT_MAX_ROUNDS) + 1):
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
        content = (resp.choices[0].message.content or "").strip()
        data = _parse_json(content)

        if not data:
            return {"answer": content or "模型没有返回可解析内容", "trace": trace}

        if "final" in data:
            return {"answer": str(data.get("final") or ""), "trace": trace}

        action = str(data.get("action") or "").strip()
        args = data.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        ok, payload = execute(action, args)
        try:
            payload_text = json.dumps(payload, ensure_ascii=False, default=str)
        except TypeError:
            payload_text = str(payload)
        trace.append(
            {
                "round": round_no,
                "action": action,
                "args": args,
                "ok": ok,
                "result": payload_text[:1200],
            }
        )
        observation = f"工具 {action} 返回：{payload_text[:1500]}"
        observations.append(observation)

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": f"观察结果：{observation}\n请继续。"})

    answer = "已达到最大工具调用轮数，以下为已获得的观察结果：\n" + "\n".join(observations)
    return {"answer": answer, "trace": trace}


__all__ = ["run"]
