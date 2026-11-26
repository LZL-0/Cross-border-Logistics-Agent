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

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT, REACT_MAX_ROUNDS
from logistics_agent.core.telemetry import live
from logistics_agent.core.telemetry.tokens import estimate, read_usage

_JSON_RE = re.compile(r"\{.*\}", re.S)
_PROMPT_LIMIT = 20000  # 单次请求保存的输入全文上限（字符），超出截断

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


def _render_prompt(messages: list[dict]) -> str:
    """把发给模型的消息按角色渲染成可读全文，供检修台查看模型输入。"""
    return "\n\n".join(
        f"【{m.get('role', '?')}】\n{m.get('content') or ''}" for m in messages
    )


def run(
    role: str,
    user_text: str,
    tools: dict[str, dict],
    execute,
    context_text: str = "",
    system_extra: str = "",
    session_id: str = "",
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
    started = time.time()
    stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "estimated": False}
    calls: list[dict] = []  # 分对话：这一轮里每一次请求的消耗与耗时

    def _finish(answer: str, error: str = "") -> dict:
        """写一条问答级 token 记录，再返回原有结构。"""
        live.usage(
            agent=role,
            session=session_id,
            model=LLM_MODEL,
            rounds=stats["calls"],
            requests=len(calls),
            calls=calls,
            prompt_tokens=stats["prompt_tokens"],
            completion_tokens=stats["completion_tokens"],
            total_tokens=stats["prompt_tokens"] + stats["completion_tokens"],
            cached_tokens=stats["cached_tokens"],
            estimated=stats["estimated"],
            latency_ms=int((time.time() - started) * 1000),
            question=str(user_text)[:120],
            answer_chars=len(str(answer)),
            error=error,
        )
        return {"answer": answer, "trace": trace}

    for round_no in range(1, max(1, REACT_MAX_ROUNDS) + 1):
        call_started = time.time()
        prompt_text = _render_prompt(messages)
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001  请求失败也要留一条分对话
            calls.append(
                {
                    "round": round_no,
                    "model": LLM_MODEL,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": int((time.time() - call_started) * 1000),
                    "estimated": False,
                    "output": "error",
                    "prompt_chars": len(prompt_text),
                    "prompt_full": prompt_text[:_PROMPT_LIMIT],
                    "prompt_truncated": len(prompt_text) > _PROMPT_LIMIT,
                    "prompt_tail": str(messages[-1].get("content") or "")[:600],
                    "content": "",
                    "tools": [],
                    "issues": [f"模型请求失败：{type(exc).__name__}: {str(exc)[:200]}"],
                }
            )
            _finish("", error=f"{type(exc).__name__}: {str(exc)[:200]}")
            raise
        call_ms = int((time.time() - call_started) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        data = _parse_json(content)

        # token 计量：优先用接口返回的 usage，缺失时按字符估算并标记
        used = read_usage(resp)
        stats["calls"] += 1
        if used["estimated"]:
            stats["estimated"] = True
            used["prompt_tokens"] = estimate("\n".join(str(m.get("content") or "") for m in messages))
            used["completion_tokens"] = estimate(content)
        stats["prompt_tokens"] += used["prompt_tokens"]
        stats["completion_tokens"] += used["completion_tokens"]
        stats["cached_tokens"] += used["cached_tokens"]
        call = {
            "round": round_no,
            "model": getattr(resp, "model", "") or LLM_MODEL,
            "prompt_tokens": used["prompt_tokens"],
            "completion_tokens": used["completion_tokens"],
            "cached_tokens": used["cached_tokens"],
            "total_tokens": used["prompt_tokens"] + used["completion_tokens"],
            "latency_ms": call_ms,
            "estimated": bool(used["estimated"]),
            "output": "final" if data and "final" in data else ("action" if data else "raw"),
            "prompt_chars": len(prompt_text),
            "prompt_full": prompt_text[:_PROMPT_LIMIT],
            "prompt_truncated": len(prompt_text) > _PROMPT_LIMIT,
            "prompt_tail": str(messages[-1].get("content") or "")[:600],
            "content": content[:2000],
            "tools": [],
            "issues": [],
        }
        if used["estimated"]:
            call["issues"].append("接口未返回 usage，token 按字符估算")
        if not data:
            call["issues"].append("模型输出无法解析为 JSON")
        calls.append(call)

        if not data:
            return _finish(content or "模型没有返回可解析内容")

        if "final" in data:
            return _finish(str(data.get("final") or ""))

        action = str(data.get("action") or "").strip()
        args = data.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if action not in tools:
            call["issues"].append(f"调用了未注册的工具：{action or '（空）'}")
        tool_started = time.time()
        ok, payload = execute(action, args)
        tool_ms = int((time.time() - tool_started) * 1000)
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
        err_text = ""
        if not ok:
            err_text = str(payload.get("error") if isinstance(payload, dict) else payload)[:600]
            call["issues"].append(f"工具 {action or '（空）'} 失败：{err_text}")
        call["tools"].append(
            {
                "name": action,
                "args": args,
                "ok": ok,
                "ms": tool_ms,
                "result": payload_text[:1200],
                "error": err_text,
            }
        )
        observation = f"工具 {action} 返回：{payload_text[:1500]}"
        observations.append(observation)

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": f"观察结果：{observation}\n请继续。"})

    answer = "已达到最大工具调用轮数，以下为已获得的观察结果：\n" + "\n".join(observations)
    return _finish(answer)


__all__ = ["run"]
