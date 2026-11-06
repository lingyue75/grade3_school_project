# 更新记录（CHANGELOG）

本项目的所有重要变更都记录在这里。格式参考 Keep a Changelog，版本遵循语义化版本。

## [0.3.0] - 2026-11-06

### 新增
- 项目说明文件包 `docs/project-brief/`：6 套说明文件，每套同时提供 `json / md / csv`：
  六人访谈记录、项目基本简报（总纲）、客户简报、现状信息请求表、
  业务发现访谈问题库、利益相关者资料与分工；另附 `README.md` 索引。
- 生成脚本 `scripts/generate_briefs.py`：以 `discovery-report.json` 为上游数据源，
  单一数据源渲染三格式，内置结构自检（文件齐备、JSON 可解析、CSV 列数一致）。
- 全部说明文件自带演练虚构声明与 f40「演练结果不得作为业务指令」边界；
  CSV 使用 `utf-8-sig` 编码并带「演练声明」列，便于单独分发。

### 内容口径
- 六人访谈逐字稿 16 轮完整保留，回答中的「尚未核实」不得补写为事实。
- 现状信息请求表 36 条全部为待补；Brief（书面）与 Industry（行业）两来源仍缺失。
- 问题库含 3 类陷阱题：占档 `missing` 不得判为未占用（f31）、不得承诺档期已改（f05/f52）、
  不得编造损失基线（REQ-6）。

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
