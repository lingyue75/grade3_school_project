# -*- coding: utf-8 -*-
"""
问答主流程（★核心复用：对应 src/services/reimbursement/chat_service.py）

职责：会话管理 → 取最近 6 轮上下文 → 混合检索 → 组装提示词 → 流式生成 → 落库 → 风险自动标记

落库约定（SQLite 上最容易踩的坑）：
    流式期间【不持有长事务】。先 insert 用户消息与 assistant 占位消息并提交，
    流式中途不写库，结束时一次性 update；中途断连则在 finally 里把已生成内容
    以 finish_reason='aborted' 落库 —— 用户刷新后仍能看到半截答案。
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterator

from . import db, llm, prompt_service
from .errors import ERRORS
from .rag import get_index
from .security import new_id, now_str
from .settings import settings


# ============================================================================
# 1. 会话
# ============================================================================
def next_seq(session_id: str) -> int:
    row = db.query_one("SELECT MAX(seq) AS m FROM messages WHERE session_id=?", (session_id,))
    return (row["m"] or 0) + 1


def create_session(user_id: str, title: str = "新会话", model: str = "") -> dict:
    sid = new_id("se")
    ts = now_str()
    db.execute(
        "INSERT INTO sessions(id,user_id,title,channel,model,message_count,token_total,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (sid, user_id, title, "web", model or settings.llm_model, 0, 0, "active", ts, ts),
    )
    return get_session(sid)


def get_session(session_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    return dict(row) if row else None


def list_sessions(user_id: str, limit: int = 50) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM sessions WHERE user_id=? AND status='active' ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    )
    return db.rows_to_dicts(rows)


def touch_session(session_id: str) -> None:
    db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_str(), session_id))


def rename_session_if_needed(session_id: str, question: str) -> None:
    """首条提问时，用问题前 20 字作为会话标题。"""
    row = db.query_one("SELECT title, message_count FROM sessions WHERE id=?", (session_id,))
    if row and (row["message_count"] or 0) <= 1:
        title = re.sub(r"\s+", " ", question).strip()[:20] or "新会话"
        db.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))


# ============================================================================
# 2. 上下文：最近 6 轮（最多 12 条）
# ============================================================================
def build_history(session_id: str, rounds: int | None = None) -> list[dict]:
    """
    取最近 N 轮对话作为上下文。
      · 1 轮 = 1 条 user + 1 条 assistant，故最多 2N 条
      · 只取 role in (user, assistant)，排除 system / tool
      · 超字符上限时【整轮丢弃】，绝不截断单条（避免半句话污染上下文）
      · 历史只带纯文本，retrieval_refs 不重复注入（否则上下文爆炸）
      · ★红线：历史仅用于指代消解，答案依据只能来自本次检索的制度原文
    """
    rounds = rounds or settings.history_rounds
    rows = db.query_all(
        "SELECT role, content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
        "AND content<>'' ORDER BY seq DESC LIMIT ?",
        (session_id, rounds * 2),
    )
    pairs = [{"role": r["role"], "content": r["content"]} for r in rows][::-1]   # 反转为时间正序

    # 字符兜底：从最早开始整轮（user+assistant）丢弃
    def size(items) -> int:
        return sum(len(i["content"]) for i in items)

    while len(pairs) >= 2 and size(pairs) > settings.history_max_chars:
        pairs = pairs[2:]
    return pairs


# ============================================================================
# 3. 提示词组装
# ============================================================================
def build_messages(question: str, sources: list[dict], system_prompt: str,
                   history: list[dict]) -> list[dict]:
    """系统提示词（来自数据库模板） + 历史 + 制度原文 + 问题。"""
    blocks = []
    for n, s in enumerate(sources, start=1):
        ref = " | ".join([p for p in [s["file"], f"条款 {s['clause_no']}" if s.get("clause_no") else "",
                                      s.get("heading_path", "")] if p])
        blocks.append(f"[{n}] 出处：{ref}\n{s['text']}")
    context = "\n\n".join(blocks) if blocks else "（本次未检索到任何制度片段）"

    return (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": f"【制度原文】\n{context}\n\n【员工问题】\n{question}"}]
    )


# ============================================================================
# 4. 风险自动标记（只标记，不判定）
# ============================================================================
def check_risk(text: str) -> list[dict]:
    """命中风险规则即返回；规则支持逗号分隔关键词，或 re: 开头的正则。"""
    rules = db.query_all("SELECT * FROM risk_rules WHERE enabled=1")
    hits = []
    for r in rules:
        pattern = r["pattern"] or ""
        hit = False
        if pattern.startswith("re:"):
            try:
                hit = re.search(pattern[3:], text) is not None
            except re.error:
                hit = False
        else:
            hit = any(k and k in text for k in pattern.split(","))
        if hit:
            hits.append({"id": r["id"], "name": r["name"], "level": r["level"]})
    return hits


def flag_message(message_id: str, session_id: str, user_id: str, rule: dict, auto: bool = True,
                 note: str = "", created_by: str = "") -> None:
    """写入风险标记（自动或人工）。系统不做违规判定，只留疑似标记与命中依据。"""
    db.execute(
        "INSERT INTO audit_flags(id,message_id,session_id,user_id,source,rule_id,note,created_by,"
        "resolved,created_at) VALUES(?,?,?,?,?,?,?,?,0,?)",
        (new_id("fl"), message_id, session_id, user_id, "auto" if auto else "manual",
         rule.get("id") if rule else None, note or (rule or {}).get("name", ""), created_by, now_str()),
    )
    db.execute("UPDATE messages SET risk_flag=1 WHERE id=?", (message_id,))


# ============================================================================
# 5. SSE 事件编码
# ============================================================================
def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_comment(text: str) -> str:
    return f": {text}\n\n"


# ============================================================================
# 6. 主流程：产出 SSE 事件流
# ============================================================================
def chat_events(user_id: str, session_id: str, question: str, request_id: str = "") -> Iterator[str]:
    """
    生成器：按接口设计 §2 的事件时序产出 SSE 文本。
    meta → status(retrieving) → sources → status(generating) → delta* → done
    异常中途：error 事件；客户端断连：finally 中把已生成内容以 aborted 落库。
    """
    started = time.time()
    index = get_index()

    # ---- 会话校验/创建 ----
    if session_id:
        sess = get_session(session_id)
        if not sess:
            yield sse("error", {"code": 2001, "message": ERRORS[2001], "request_id": request_id})
            return
        if sess["user_id"] != user_id:
            yield sse("error", {"code": 1102, "message": ERRORS[1102], "request_id": request_id})
            return
        if sess["status"] == "archived":
            yield sse("error", {"code": 2002, "message": ERRORS[2002], "request_id": request_id})
            return
    else:
        sess = create_session(user_id, model=settings.llm_model)
        session_id = sess["id"]

    # ---- 落库：用户消息 + assistant 占位（短事务，立即提交） ----
    user_msg_id = new_id("ms")
    seq = next_seq(session_id)
    db.execute(
        "INSERT INTO messages(id,session_id,seq,role,content,created_at) VALUES(?,?,?,?,?,?)",
        (user_msg_id, session_id, seq, "user", question, now_str()),
    )
    assistant_msg_id = new_id("ms")
    system_prompt, tpl_id, tpl_version = prompt_service.build_system_prompt()
    db.execute(
        "INSERT INTO messages(id,session_id,seq,role,content,model,prompt_version_id,finish_reason,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (assistant_msg_id, session_id, seq + 1, "assistant", "", settings.llm_model, tpl_id, "running", now_str()),
    )
    db.execute("UPDATE sessions SET message_count=message_count+2, updated_at=?, prompt_version_id=? WHERE id=?",
               (now_str(), tpl_id, session_id))
    rename_session_if_needed(session_id, question)

    yield sse("meta", {"message_id": assistant_msg_id, "session_id": session_id,
                       "created_at": now_str(), "request_id": request_id})

    # ---- 检索 ----
    yield sse("status", {"stage": "retrieving", "text": "正在检索制度…"})
    sources = index.search(question, settings.top_k)
    yield sse("sources", {"sources": [
        {"no": i, "chunk_id": s["chunk_id"], "file": s["file"], "clause_no": s["clause_no"],
         "heading_path": s["heading_path"], "snippet": s["text"][:120], "score": s["score"],
         "low_confidence": s["low_confidence"]}
        for i, s in enumerate(sources, start=1)
    ]})

    refs = [{"no": i, "chunk_id": s["chunk_id"], "file": s["file"], "clause_no": s["clause_no"],
             "heading_path": s["heading_path"], "snippet": s["text"][:120]}
            for i, s in enumerate(sources, start=1)]

    # ---- 检索为空：业务性结果，HTTP 仍 200，按普通消息渲染 ----
    if not sources:
        text = ERRORS[3002]
        db.execute("UPDATE messages SET content=?, retrieval_refs=?, finish_reason=?, latency_ms=?, error_text=? WHERE id=?",
                   (text, json.dumps([], ensure_ascii=False), "stop", int((time.time() - started) * 1000),
                    "no_source", assistant_msg_id))
        yield sse("delta", {"content": text})
        yield sse("done", {"message_id": assistant_msg_id, "finish_reason": "stop", "code": 3002,
                           "usage": {"prompt_tokens": 0, "completion_tokens": len(text)},
                           "latency_ms": int((time.time() - started) * 1000)})
        yield "data: [DONE]\n\n"
        return

    # ---- 生成 ----
    yield sse("status", {"stage": "generating", "text": "正在生成回答…"})
    history = build_history(session_id)
    messages = build_messages(question, sources, system_prompt, history)

    accumulated: list[str] = []
    finished = False
    try:
        for chunk in llm.stream_generate(messages):
            accumulated.append(chunk)
            yield sse("delta", {"content": chunk})
        finished = True
    except Exception as exc:                                    # 模型不可用：4001
        err = str(exc)
        db.execute("UPDATE messages SET content=?, finish_reason=?, error_text=?, latency_ms=? WHERE id=?",
                   ("", "error", err[:500], int((time.time() - started) * 1000), assistant_msg_id))
        yield sse("error", {"code": 4001, "message": ERRORS[4001], "detail": err[:200],
                            "request_id": request_id})
        yield "data: [DONE]\n\n"
        return
    finally:
        # 客户端断连（GeneratorExit）时也会走到这里：把已生成内容保住，绝不丢答案
        if not finished and accumulated:
            db.execute("UPDATE messages SET content=?, retrieval_refs=?, finish_reason=?, latency_ms=? WHERE id=?",
                       ("".join(accumulated), json.dumps(refs, ensure_ascii=False), "aborted",
                        int((time.time() - started) * 1000), assistant_msg_id))

    # ---- 正常收尾 ----
    answer = "".join(accumulated)
    latency = int((time.time() - started) * 1000)
    db.execute(
        "UPDATE messages SET content=?, retrieval_refs=?, finish_reason=?, latency_ms=? WHERE id=?",
        (answer, json.dumps(refs, ensure_ascii=False), "stop", latency, assistant_msg_id),
    )

    # ---- 风险自动标记：只标记，不做违规判定 ----
    hits = check_risk(question + "\n" + answer)
    for h in hits:
        flag_message(assistant_msg_id, session_id, user_id, h, auto=True, note=f"命中规则：{h['name']}")

    yield sse("done", {
        "message_id": assistant_msg_id,
        "finish_reason": "stop",
        "usage": {"prompt_tokens": sum(len(m["content"]) for m in messages) // 2,
                  "completion_tokens": len(answer) // 2},
        "latency_ms": latency,
        "risk_hit": bool(hits),
    })
    yield "data: [DONE]\n\n"
    touch_session(session_id)


# ============================================================================
# 7. 非流式（在线试问 / 评测用）
# ============================================================================
def chat_once(question: str, system_prompt: str | None = None, top_k: int | None = None) -> dict:
    """一次问答，返回 {answer, sources, latency_ms}；不落库，供后台"在线试问"与评测使用。"""
    started = time.time()
    index = get_index()
    sources = index.search(question, top_k or settings.top_k)
    prompt = system_prompt or prompt_service.build_system_prompt()[0]
    messages = build_messages(question, sources, prompt, [])
    answer = llm.generate(messages)
    return {"answer": answer, "sources": sources, "latency_ms": int((time.time() - started) * 1000)}
