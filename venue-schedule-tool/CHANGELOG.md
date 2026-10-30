# 更新记录（CHANGELOG）

本项目的所有重要变更都记录在这里。格式参考 Keep a Changelog，版本遵循语义化版本。

## [0.2.0] - 2026-10-30

### 新增
- Agent 编排包 `src/venue_schedule_tool/agent/`：
  - `tools.py` 10 个工具（query_slots / get_slot / detect_conflict / check_handover /
    validate_evidence / assess_change / draft_change_request / submit_handover /
    request_authorization / ask_user）+ 校验令牌 stamp + 工具动态可见性
  - `planner.py` 决策器：规则策略（离线）+ 真实 LLM Function Calling
  - `loop.py` 执行循环：自主阶段 5 轮 + 变更请求的授权人确认中断与恢复
  - `store.py` SQLite 持久化：agent_runs / agent_step_logs / change_requests
- 三条典型轨迹演示 `examples/agent_demo.py`（含防跳步与锁档拦截验证）
- Agent 护栏回归测试 `tests/test_agent.py`（对齐 f05/f31/f40/f41/f52）
- 设计文档 `docs/Agent设计-工具与约束.md`

### 边界
- 工具集中【永不提供】lock_slot / sign_contract / commit_capacity 等能力
- 变更请求必须经授权人员（傅景/顾明岚）确认，系统不代为决定
- 状态缺失（missing）只报"数据不完整"，不得判定为未占用

## [0.1.0] - 2026-10-23

### 新增
- 统一关联编号模块 `core/registry.py`（`DLG-YYYYMMDD-NNNN`），串联服务台/台账/文件库（对应 REQ-2）
- 冲突检测模块 `core/conflict.py`：区域时间窗重叠、装卸口进/撤场重叠、交接版本不一致、交接三项不齐套（对应 REQ-1）
- 人工复核留痕模块 `core/review.py`：授权确认 + 来源校验（对应 REQ-3）
- 30 样本回放 demo 与命令行入口（对齐验收口径 f02）
- pytest 测试套件；样例数据 `data/slots.csv`、`data/handovers.csv`
- 业务文档：业务挖掘报告、行业分析、设计方案、报价单、两张流程图（docs/）

### 边界
- 只提示冲突，不自动锁档/签约/承诺容量（f05/f52）
- 演练输出不得作为实际业务指令（f40）
