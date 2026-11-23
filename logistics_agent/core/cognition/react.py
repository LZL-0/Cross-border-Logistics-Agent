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

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT, REACT_MAX_ROUNDS

_JSON_RE = re.compile(r"\{.*\}", re.S)

_SYSTEM_TMPL = """你是「跨境物流引擎」的智能体，角色：{role}。
只做跨境运输方案、清关时效、税费与比价相关的分析与计算，不编造运价与税率。

可用工具（名称：说明；参数：调用时必须使用的英文参数名）：
{tools}

输出格式（严格遵守，只输出一个 JSON，不要 Markdown 代码块）：
- 需要调用工具：{{"action": "工具名", "args": {{参数对象}}}}
- 可以结束：{{"final": "给用户的中文回答，含关键数字与假设"}}
args 的键必须与上面列出的参数名完全一致，不要自造 origin/destination 等字段。
一次只调用一个工具；拿到观察结果后再决定下一步。最多 {rounds} 轮。
"""


def _params_of(fn) -> str:
    """抽取工具函数参数名与默认值，供模型照抄，避免自造关键字。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return ""
    parts = []
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            parts.append(p.name)
        else:
            parts.append(f"{p.name}={p.default!r}")
    return ", ".join(parts)


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
    tool_doc = "\n".join(
        f"- {name}：{item['description']}；参数：({_params_of(item.get('func'))})"
        for name, item in tools.items()
    )
    if not tool_doc:
        tool_doc = "（当前角色没有可用工具）"

    system = _SYSTEM_TMPL.format(role=role, tools=tool_doc, rounds=REACT_MAX_ROUNDS)
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
            temperature=0.2,
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
