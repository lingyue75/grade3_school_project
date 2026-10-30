# -*- coding: utf-8 -*-
"""
大模型调用层（★核心复用：对应 src/agent/llm/base.py + provider_openai.py）

对外只暴露两个函数：
    generate(messages) -> str              非流式（评测、在线试问用）
    stream_generate(messages) -> Iterator  流式（SSE 用）

供应商可配置（settings.llm_provider）：
    mock   —— 内置模拟模型，离线跑通全链路（演示/联调/无 Key 环境）；基于检索到的条文
              做"抽取式"作答，绝不编造制度外的数字
    openai —— OpenAI 兼容协议，覆盖 DeepSeek / 通义 / 豆包 / 本地 vLLM
"""

from __future__ import annotations

import re
import time
from typing import Iterator

from .settings import settings


# ============================================================================
# 1. 非流式
# ============================================================================
def generate(messages: list[dict], temperature: float | None = None, max_tokens: int | None = None) -> str:
    """一次性生成；失败抛出带中文说明的异常（由上层转错误码 4001/4002）。"""
    if settings.llm_provider == "mock":
        return _mock_answer(messages)
    return _openai_generate(messages, temperature, max_tokens)


# ============================================================================
# 2. 流式
# ============================================================================
def stream_generate(messages: list[dict], temperature: float | None = None,
                    max_tokens: int | None = None) -> Iterator[str]:
    """流式产出文本片段；无论底层供应商是谁，对上层都是统一的文本迭代器。"""
    if settings.llm_provider == "mock":
        yield from _mock_stream(messages)
        return

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("未安装 openai SDK，请执行 pip install openai，或将 LLM_PROVIDER 设为 mock") from exc

    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY（或将 LLM_PROVIDER 设为 mock 以离线运行）")

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.llm_timeout)
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=settings.temperature if temperature is None else temperature,
            max_tokens=settings.max_tokens if max_tokens is None else max_tokens,
            stream=True,
        )
    except Exception as exc:
        raise RuntimeError(f"模型调用失败（{settings.llm_base_url} / {settings.llm_model}）：{exc}") from exc

    for chunk in resp:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta


def _openai_generate(messages: list[dict], temperature: float | None, max_tokens: int | None) -> str:
    from openai import OpenAI
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY（或将 LLM_PROVIDER 设为 mock 以离线运行）")
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.llm_timeout)
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=settings.temperature if temperature is None else temperature,
        max_tokens=settings.max_tokens if max_tokens is None else max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# ============================================================================
# 3. 内置模拟模型（仅用于离线跑通链路）
#     策略：从检索到的制度原文中抽取与问题最相关的句子，原样引用 + 标注引用编号。
#     绝不生成原文中不存在的金额与流程 —— 与正式模型的红线保持一致。
# ============================================================================
_SENT_SPLIT = re.compile(r"[。；!?！？\n]")
_TERM = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}")


def _extract_terms(question: str) -> list[str]:
    return [t for t in _TERM.findall(question) if len(t) >= 2]


def _mock_answer_from_sources(question: str, sources: list[dict]) -> str:
    if not sources:
        return "制度中未找到相关规定，建议咨询财务部门。"

    terms = _extract_terms(question)
    picks: list[str] = []

    for i, s in enumerate(sources, start=1):
        sentences = [x.strip() for x in _SENT_SPLIT.split(s.get("text", "")) if len(x.strip()) >= 8]
        if not sentences:
            continue
        # 选与问题关键词重合度最高的句子（模拟"依据原文作答"，不做任何推理）
        best = max(sentences, key=lambda x: sum(1 for t in terms if t in x))
        if best and any(t in best for t in terms):
            picks.append(f"{best}。 [{i}]")

    if not picks:
        return ("制度中未找到与该问题直接对应的条款，建议咨询财务部门。"
                "（以下为最相关的条文，供参考）\n"
                + "\n".join(f"[{i}] {s['file']}"
                            + (f" 条款{s['clause_no']}" if s.get("clause_no") else "")
                            for i, s in enumerate(sources, start=1)))

    body = "".join(picks)
    refs = "\n来源：" + "；".join(
        f"[{i}] {s['file']}" + (f" 条款{s['clause_no']}" if s.get("clause_no") else "")
        for i, s in enumerate(sources, start=1)
    )
    return body + refs


def _mock_answer(messages: list[dict]) -> str:
    """从 messages 里还原问题与检索来源，生成抽取式答案。"""
    question, sources = _parse_messages(messages)
    return _mock_answer_from_sources(question, sources)


def _mock_stream(messages: list[dict]) -> Iterator[str]:
    """把模拟答案按小片段吐出，模拟真实流式效果（前端打字机依赖这个节奏）。"""
    text = _mock_answer(messages)
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= 3 or ch in "。，；\n":
            yield buf
            buf = ""
            time.sleep(0.012)          # 轻微节流，模拟网络到达节奏
    if buf:
        yield buf


def _parse_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    从提示词里反解出问题与来源（仅 mock 使用）。
    正式实现中，来源由调用方直接以结构化参数传入，不需要反解。
    """
    question = ""
    sources: list[dict] = []
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            # 约定：用户消息最后一段为【员工问题】
            if "【员工问题】" in content:
                question = content.split("【员工问题】")[-1].strip()
            else:
                question = content.strip()
            # 解析 [n] 出处：xxx 形式的来源块
            for block in re.findall(r"\[(\d+)\]\s*出处：([^\n]+)", content):
                idx, ref = int(block[0]), block[1]
                parts = [p.strip() for p in ref.split("|")]
                sources.append({
                    "file": parts[0] if parts else "制度文件",
                    "clause_no": next((p.replace("条款 ", "") for p in parts if p.startswith("条款")), ""),
                    "text": "",
                })
            # 取每条来源块后面的正文
            bodies = re.split(r"\[\d+\]\s*出处：[^\n]+\n", content)
            for i, b in enumerate(bodies[1:]):
                if i < len(sources):
                    sources[i]["text"] = b.split("\n\n")[0].strip()
    return question, sources
