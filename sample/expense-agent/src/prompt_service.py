# -*- coding: utf-8 -*-
"""
提示词模板服务（★核心复用：对应 src/services/ 中的模板管理）

关键设计（对应接口设计 §0 坑 2）：
  · 模板【只增版本、不改历史】：已发布版本的正文禁止修改（错误码 5303），
    保证任何一次历史问答都能回放"当时用的是哪一版提示词"。
  · 生效机制：进程内缓存 + 版本号比对 —— 发布即失效，60 秒兜底过期。
    既满足"修改后立即对问答生效"，又不会每次请求都打数据库。
"""

from __future__ import annotations

import time

from . import db
from .errors import BizError
from .security import new_id, now_str
from .settings import settings

_CACHE: dict = {"data": None, "ts": 0}
_CACHE_TTL = 60          # 秒：兜底过期，防止极端情况下缓存不失效
MAX_PROMPT_LEN = 4000


# ============================================================================
# 1. 读取生效模板
# ============================================================================
def get_active(use_cache: bool = True) -> dict:
    """返回当前生效模板 {id, name, content, version_no}；无模板时返回内置兜底文案。"""
    if use_cache and _CACHE["data"] and (time.time() - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]

    row = db.query_one(
        "SELECT id, name, content, version_no FROM prompt_templates WHERE is_active=1 ORDER BY version_no DESC LIMIT 1"
    )
    data = dict(row) if row else {
        "id": "", "name": "内置兜底", "version_no": 0,
        "content": "你是公司报销制度助手，只根据提供的制度条文回答，不确定时明确说'制度中未找到'。",
    }
    _CACHE["data"] = data
    _CACHE["ts"] = time.time()
    return data


def invalidate_cache() -> None:
    """发布/回滚后调用，使缓存立即失效（下一问起生效）。"""
    _CACHE["data"] = None
    _CACHE["ts"] = 0


def get_active_for_display() -> dict:
    """后台展示用：强制读库，避免看到缓存里的旧值。"""
    return get_active(use_cache=False)


# ============================================================================
# 2. 列表 / 详情
# ============================================================================
def list_templates() -> list[dict]:
    rows = db.query_all(
        "SELECT t.*, u.name AS creator FROM prompt_templates t "
        "LEFT JOIN users u ON u.id=t.created_by ORDER BY t.version_no DESC"
    )
    return db.rows_to_dicts(rows)


def get_template(template_id: str) -> dict:
    row = db.query_one("SELECT * FROM prompt_templates WHERE id=?", (template_id,))
    if not row:
        raise BizError(5301)
    return dict(row)


# ============================================================================
# 3. 写操作
# ============================================================================
def _validate_content(content: str) -> str:
    content = (content or "").strip()
    if not content:
        raise BizError(5302, "模板内容不能为空")
    if len(content) > MAX_PROMPT_LEN:
        raise BizError(5302, f"模板内容超过 {MAX_PROMPT_LEN} 字限制")
    return content


def create_template(name: str, content: str, user_id: str, note: str = "") -> dict:
    """新建模板：基于当前最大版本号 +1，状态为草稿。"""
    content = _validate_content(content)
    row = db.query_one("SELECT MAX(version_no) AS v FROM prompt_templates")
    version = (row["v"] or 0) + 1
    tid = new_id("pt")
    ts = now_str()
    db.execute(
        "INSERT INTO prompt_templates(id,name,content,version_no,status,is_active,created_by,note,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (tid, (name or f"模板 v{version}").strip(), content, version, "draft", 0, user_id, note, ts),
    )
    db.write_log(user_id, "prompt.create", "prompt_template", tid, {"version_no": version})
    return get_template(tid)


def update_draft(template_id: str, content: str, name: str | None, user_id: str) -> dict:
    """编辑草稿。已发布版本拒绝修改（5303）—— 保证历史问答可回放。"""
    tpl = get_template(template_id)
    if tpl["status"] != "draft":
        raise BizError(5303)
    content = _validate_content(content)
    db.execute(
        "UPDATE prompt_templates SET content=?, name=? WHERE id=?",
        (content, (name or tpl["name"]).strip(), template_id),
    )
    db.write_log(user_id, "prompt.update", "prompt_template", template_id, {"version_no": tpl["version_no"]})
    return get_template(template_id)


def publish(template_id: str, user_id: str) -> dict:
    """发布：置为 published，并成为唯一生效版本（其他版本 is_active 清零）。"""
    tpl = get_template(template_id)
    db.execute("UPDATE prompt_templates SET is_active=0")
    db.execute(
        "UPDATE prompt_templates SET status='published', is_active=1, published_at=? WHERE id=?",
        (now_str(), template_id),
    )
    invalidate_cache()          # ★ 发布后立即对问答生效
    db.write_log(user_id, "prompt.publish", "prompt_template", template_id, {"version_no": tpl["version_no"]})
    return get_template(template_id)


def activate(template_id: str, user_id: str) -> dict:
    """回滚：把某个历史版本设为生效（不改内容，只切指针）。"""
    tpl = get_template(template_id)
    if tpl["status"] == "archived":
        raise BizError(5301, "已归档的模板无法设为生效")
    db.execute("UPDATE prompt_templates SET is_active=0")
    db.execute("UPDATE prompt_templates SET status='published', is_active=1 WHERE id=?", (template_id,))
    invalidate_cache()
    db.write_log(user_id, "prompt.activate", "prompt_template", template_id, {"version_no": tpl["version_no"]})
    return get_template(template_id)


def archive(template_id: str, user_id: str) -> None:
    """归档：不删除，保留审计链；若它是生效版本则先取消生效。"""
    tpl = get_template(template_id)
    if tpl["is_active"]:
        db.execute("UPDATE prompt_templates SET is_active=0 WHERE id=?", (template_id,))
        invalidate_cache()
    db.execute("UPDATE prompt_templates SET status='archived' WHERE id=?", (template_id,))
    db.write_log(user_id, "prompt.archive", "prompt_template", template_id, {"version_no": tpl["version_no"]})


# ============================================================================
# 4. 组装最终系统提示词（模板 + 输出格式约束）
#     注意：管理员只编辑"制度助手"的角色约束，输出格式/引用规范由系统固定追加，
#     避免管理员误删导致答案失去可溯源性。
# ============================================================================
OUTPUT_RULES = """

【输出规范（系统固定，不可编辑）】
1. 每个结论后标注引用编号，格式如 [1]。
2. 涉及金额、天数、比例时原样引用原文数字，不得换算或四舍五入。
3. 若存在多个适用标准（如按职级区分），先说明判断依据再给结论。
4. 回答控制在 200 字以内，除非用户要求展开。"""


def build_system_prompt() -> tuple[str, str, int]:
    """返回 (系统提示词全文, 模板ID, 模板版本号)，供 /chat 落库与审计使用。"""
    tpl = get_active()
    return tpl["content"] + OUTPUT_RULES, tpl.get("id", ""), tpl.get("version_no", 0)


def reload_if_needed() -> None:
    """进程内缓存自检（可由定时任务调用），settings.debug 下也可手动触发。"""
    row = db.query_one("SELECT id FROM prompt_templates WHERE is_active=1 ORDER BY version_no DESC LIMIT 1")
    active_id = row["id"] if row else ""
    if _CACHE["data"] and _CACHE["data"].get("id") != active_id:
        invalidate_cache()
