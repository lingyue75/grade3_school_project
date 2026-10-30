# -*- coding: utf-8 -*-
"""
统一错误码与中文错误信息（★核心复用：对应 src/core/errors.py）

约定：
  · 所有接口返回 {code, message, data, request_id}
  · code = 0 表示成功，其余见下表
  · message 一律中文，且【可直接展示给终端用户】，不含堆栈与内部细节
  · 3002 / 4003 属于"业务性结果"，HTTP 仍返回 200，前端按普通消息渲染而非弹错误框
"""

from __future__ import annotations

from typing import Any

# ============================ 错误码表 ============================
# 0    成功
# 10xx 通用/参数
# 11xx 鉴权
# 12xx 限流
# 20xx 会话
# 30xx 检索/知识库
# 40xx 模型
# 50xx 服务内部
# 51xx 管理-权限
# 52xx 管理-知识库
# 53xx 管理-提示词
# 54xx 管理-审计
ERRORS: dict[int, str] = {
    0: "成功",

    1001: "请求参数不完整或格式不正确",
    1002: "问题内容过长，请精简至 1000 字以内",
    1003: "请求格式无法解析",

    1101: "登录状态已失效，请重新登录",
    1102: "无权访问该会话",
    1103: "工号或密码错误",
    1104: "账号已被锁定，请稍后再试",
    1105: "CSRF 校验失败，请刷新页面后重试",

    1201: "操作过于频繁，请稍后再试",

    2001: "会话不存在或已被删除",
    2002: "会话已归档，无法继续提问",

    3001: "制度知识库尚未初始化，请联系管理员上传制度文档",
    3002: "未在制度中检索到相关规定，建议咨询财务部门",

    4001: "模型服务暂时不可用，请稍后重试",
    4002: "模型响应超时，请重试",
    4003: "回答未通过合规校验，已终止输出",

    5000: "服务内部错误，请联系技术支持",

    5101: "需要管理员权限才能执行该操作",

    5201: "仅支持上传 Markdown 或 PDF 文件",
    5202: "文件大小超过限制",
    5203: "文档解析失败，请确认文件未加密且包含可复制文本",
    5204: "向量化失败，请检查 embedding 服务配置",
    5205: "文档不存在或已被删除",
    5206: "该文档已存在，请勿重复上传",

    5301: "提示词模板不存在",
    5302: "模板内容为空或超过长度限制",
    5303: "已发布的版本不可修改，请新建版本后发布",

    5401: "审计记录不存在",
    5402: "标记说明不能为空",
}

# 这些 code 属于"业务性结果"：HTTP 仍返回 200，前端当普通消息处理
SOFT_CODES = {3002, 4003}


class BizError(Exception):
    """业务异常：携带错误码，由全局异常处理转成统一信封。"""

    def __init__(self, code: int, message: str | None = None, http_status: int | None = None):
        self.code = code
        self.message = message or ERRORS.get(code, "未知错误")
        # 未显式指定时：软错误返回 200，其余按类别映射
        self.http_status = http_status or self._default_status(code)
        super().__init__(self.message)

    @staticmethod
    def _default_status(code: int) -> int:
        if code == 0 or code in SOFT_CODES:
            return 200
        if 1000 <= code < 1100:
            return 400
        if code in (1101, 1103):
            return 401
        if code in (1102, 1104, 1105, 5101):
            return 403
        if code == 1201:
            return 429
        if code in (2001, 5205, 5301, 5401):
            return 404
        if code in (4001, 4002, 5204):
            return 502
        if code == 5206:
            return 409
        return 500


def ok(data: Any = None, message: str = "成功", request_id: str = "") -> dict:
    """成功响应信封。"""
    return {"code": 0, "message": message, "data": data if data is not None else {}, "request_id": request_id}


def fail(code: int, message: str | None = None, request_id: str = "") -> dict:
    """失败响应信封（用于手工构造，如 SSE 的 error 事件）。"""
    return {
        "code": code,
        "message": message or ERRORS.get(code, "未知错误"),
        "data": {},
        "request_id": request_id,
    }
