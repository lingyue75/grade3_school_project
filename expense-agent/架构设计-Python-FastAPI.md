# 报销助手智能体系统 · 技术架构方案（Python / FastAPI）

> 版本 v0.1 ｜ 2026-10-30 ｜ 约束：FastAPI 异步、Python 3.11+、LLM 可替换、制度知识库向量检索、两角色（员工 / 管理员）
> 范围：本方案聚焦「对话 + 知识库 + 草稿」的骨架，预算占用与 OA 审批按 v1.0 需求清单列为二期接入点，此处只预留接口位。

---

## 0. 先说三个判断

1. **本项目唯一可能失败的地方是 RAG 准确率和工具调用稳定性**，不是 CRUD、不是权限。所以第一周必须先把这两件事证伪，其余都是确定性工作。
2. **制度知识库不能按字数切片**。报销制度是条款化文本，必须按「条款 / 条目」切，且每片带结构化元数据（适用职级、生效区间、制度版本），检索时做**结构化过滤 + 向量召回**，否则实习生会检索到高管的差旅标准。
3. **纯 Python 栈完全可行**，但两个点必须注意：金额与额度并发交给 Postgres（事务 + 行锁 / `SELECT FOR UPDATE`），不要在 Python 层做计数器；PDF/OFD 解析、向量化是 CPU 密集，一律丢 Celery worker，不要堵住 async 事件循环。

---

## 1. 四层架构图（文字版）

```
┌────────────────────────────────────────────────────────────────────────────┐
│ ① 入口层  Interface Layer                                                    │
│                                                                              │
│  FastAPI ASGI App（uvicorn / gunicorn+uvicorn worker）                        │
│  路由：/auth  /chat(SSE 流式)  /sessions  /reimbursement  /invoices           │
│        /knowledge(管理员)  /admin  /webhooks/approval(OA 回调)                │
│                                                                              │
│  中间件链（顺序即责任链）：                                                    │
│   RequestID → 认证(JWT/OIDC) → 用户上下文 → RBAC(employee/admin)             │
│   → 速率限制 → 审计埋点 → 异常统一封装                                        │
│                                                                              │
│  依赖注入：CurrentUser / DbSession / LLMProvider / VectorStore / Storage      │
│  协议：REST + SSE（不用 WebSocket，简单、可断点、易观测）                      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ 只调 Service，不直接碰 ORM / LLM
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ ② 服务层  Service Layer（用例编排 + 领域规则，纯业务，不感知 HTTP）            │
│                                                                              │
│  UserService        职级/部门主数据同步，用户与角色                            │
│  SessionService     会话生命周期、上下文窗口、标题生成                         │
│  KnowledgeService   文档摄取：解析 → 条款切片 → 元数据标注 → 入向量库          │
│  RetrievalService   混合检索（向量 + BM25）+ Rerank + 结构化过滤 + 引用裁剪     │
│  ReimbursementService 草稿 CRUD、状态机、提交前校验、快照写定                   │
│  InvoiceService     票据归集、解析、四元组查重、状态机                         │
│  RuleService        硬规则求值（配置化规则表 + 表达式），返回阻断/警告          │
│  BudgetService      额度冻结/核销/释放（二期接，此处留接口）                   │
│  ApprovalService    OA 发起、回调幂等、状态同步、定时对账（二期接）             │
│  AuditService       谁/何时/用哪条规则版本/做了什么决定                        │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ 服务层不直接调 LLM，需要"理解/生成"才下沉到编排层
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ ③ 智能体编排层  Agent Orchestration Layer                                     │
│                                                                              │
│  Router(意图分类)  ──►  qa(制度问答) / fill_form(填单) / query_status / fallback│
│         │                                                                     │
│         ▼                                                                     │
│  Orchestrator（LangGraph 或自研状态机，显式状态、可回放、max_steps 熔断）      │
│         │                                                                     │
│         ├── Retriever（调 RetrievalService，返回带条款号的引用块）              │
│         ├── Tool Registry（工具即能力边界）                                    │
│         │     search_policy / calc_allowance / create_draft                   │
│         │     add_item / check_rules / submit_draft / query_status            │
│         ├── Memory（短期：消息窗口 + 滚动摘要；长期：用户偏好，一期可省）       │
│         └── Guardrails                                                        │
│              输入侧：PII 脱敏、提示注入防护、越权意图拦截                      │
│              输出侧：禁止独立放行、禁止承诺金额、必须附条款引用                 │
│         │                                                                     │
│         ▼                                                                     │
│  LLM Provider 抽象层（统一 chat / stream / embedding 协议）                    │
│     OpenAI 兼容适配 → DeepSeek / 通义 / 豆包 / 私有 vLLM 可热切换               │
│     + 重试 / 超时 / 降级 fallback / token 计费 / 全量调用留痕                  │
│  Prompt 管理：版本化模板文件（prompts/v1/*.md），运行期记录 prompt_version      │
│  Observability：每次调用记录 prompt、检索片段、工具链、输出 → 可回溯评估        │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ ④ 数据模型层  Persistence Layer                                               │
│                                                                              │
│  PostgreSQL 16 + pgvector  业务关系数据 + 制度向量（同库，省一个组件）          │
│     用户 / 会话 / 消息 / 制度文档 / 文档分片(embedding) / 报销单草稿 / 票据     │
│     规则 / 额度占用 / 审计日志                                                 │
│  Redis        会话缓存、SSE 断线续传、限流、Celery broker                      │
│  对象存储     原始制度文档、发票影像（S3 协议抽象，可换 OSS/MinIO）             │
│  Celery       文档摄取、向量化、票据归集、OA 对账（CPU/IO 密集全部异步）        │
│  Alembic      迁移；SQLAlchemy 2.0 async + asyncpg                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

**层间纪律**：入口层不碰 ORM；服务层不碰 LLM；编排层只通过 Tool 调服务，不绕过服务直接写库；所有跨层调用带 request_id 与 trace_id。

---

## 2. 推荐目录结构

```
expense-agent/
├── pyproject.toml                     # uv / poetry 管理，锁版本
├── Dockerfile
├── docker-compose.yml                 # pg+pgvector / redis / minio / api / worker
├── .env.example
├── alembic/
│   └── versions/
├── src/
│   ├── main.py                        # FastAPI app factory（只做装配）
│   ├── core/                          # 横切关注点
│   │   ├── config.py                  # pydantic-settings，环境隔离
│   │   ├── security.py                # JWT / OIDC、密码哈希
│   │   ├── rbac.py                    # 角色断言（employee / admin）
│   │   ├── logging.py                 # structlog + request_id
│   │   ├── errors.py                  # 统一异常与业务错误码
│   │   └── deps.py                    # 全局依赖注入
│   ├── api/                           # ① 入口层
│   │   ├── middleware/
│   │   │   ├── request_context.py
│   │   │   └── audit.py
│   │   └── v1/
│   │       ├── auth.py
│   │       ├── chat.py                # SSE 流式对话
│   │       ├── sessions.py
│   │       ├── reimbursement.py       # 草稿 CRUD / 校验 / 提交
│   │       ├── invoices.py
│   │       ├── knowledge.py           # 管理员：文档上传、重建索引
│   │       ├── admin.py               # 用户、职级标准、规则配置
│   │       └── webhooks/
│   │           └── approval.py        # OA 回调（幂等 + 签名校验）
│   ├── schemas/                       # Pydantic v2 DTO（请求/响应，不复用 ORM）
│   │   ├── user.py  chat.py  reimbursement.py  knowledge.py
│   ├── domain/                        # 领域模型
│   │   ├── models/                    # SQLAlchemy ORM
│   │   │   ├── user.py  session.py  message.py
│   │   │   ├── knowledge.py          # policy_documents / policy_chunks
│   │   │   ├── reimbursement.py      # drafts / draft_items
│   │   │   ├── invoice.py  rule.py  budget.py  approval.py  audit.py
│   │   ├── enums.py
│   │   └── events.py                 # 领域事件（草稿提交、规则阻断…）
│   ├── services/                      # ② 服务层
│   │   ├── user_service.py
│   │   ├── session_service.py
│   │   ├── knowledge_service.py       # 摄取：解析 → 切片 → 标注 → 入库
│   │   ├── retrieval_service.py       # 混合检索 + rerank + 结构化过滤
│   │   ├── reimbursement_service.py
│   │   ├── invoice_service.py
│   │   ├── rule_service.py            # 硬规则求值（确定性，不放给 LLM）
│   │   ├── budget_service.py          # 冻结/核销/释放（二期接）
│   │   ├── approval_service.py        # OA 适配 + 对账（二期接）
│   │   └── audit_service.py
│   ├── agent/                         # ③ 智能体编排层
│   │   ├── graph.py                   # 状态机编排（LangGraph 或自研）
│   │   ├── state.py                   # AgentState（含 trace / step 计数）
│   │   ├── router.py                  # 意图分类
│   │   ├── prompts/
│   │   │   └── v1/intent.md  qa.md  fill_form.md  guardrail.md
│   │   ├── tools/
│   │   │   ├── registry.py
│   │   │   ├── policy_search.py
│   │   │   ├── draft_tools.py         # create / add_item / submit
│   │   │   ├── allowance_calc.py      # 按职级算标准
│   │   │   └── rule_check.py
│   │   ├── guardrails/
│   │   │   ├── input_filter.py        # PII 脱敏、注入防护
│   │   │   └── output_filter.py       # 禁止独立放行 / 金额承诺
│   │   ├── memory/
│   │   │   ├── short_term.py          # 消息窗口 + 滚动摘要
│   │   │   └── long_term.py
│   │   └── llm/
│   │       ├── base.py                # chat / stream / embedding 协议
│   │       ├── provider_openai.py     # OpenAI 兼容（覆盖多数供应商）
│   │       ├── provider_fallback.py
│   │       └── token_usage.py
│   ├── infra/                         # 基础设施适配（可替换实现）
│   │   ├── db.py  redis.py  storage.py
│   │   ├── vectorstore/ base.py  pgvector.py
│   │   ├── embedding.py               # embedding 模型，可替换
│   │   └── queue.py                   # Celery / ARQ
│   ├── tasks/                         # 异步任务（worker 进程）
│   │   ├── ingest_document.py
│   │   ├── embed_chunks.py
│   │   ├── collect_invoices.py
│   │   └── reconcile_approval.py
│   └── observability/
│       ├── tracing.py                 # OpenTelemetry
│       └── llm_trace.py               # prompt/检索/工具链/输出 全量留痕
├── tests/
│   ├── unit/  integration/  e2e/
│   └── eval/
│       ├── golden_qa.yaml             # 制度问答黄金集（≥20 条，含期望条款号）
│       ├── golden_fill.yaml           # 填单用例集
│       └── run_eval.py                # 评测脚本，出准确率基线
├── web/                               # Vue3（可独立仓库，第一周用最简页面）
└── docs/
    ├── architecture.md
    └── api-contract.md
```

**目录设计原则**：`domain/models` 与 `schemas` 分离（ORM 不外泄）；`agent/` 完全独立于 `services/`（换编排框架不动业务）；`infra/` 全部面向接口（换向量库/对象存储/模型商只改这一层）。

---

## 3. 数据库表设计

> 通用约定：主键 UUID v7（时间有序，索引友好）；时间统一 `timestamptz`；**金额一律 `numeric(14,2)`，禁止 float**；软删除用 `deleted_at`；所有表带 `created_at / updated_at`。

### 3.1 users（用户）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| employee_no | varchar(32) UNIQUE | 工号，主数据对齐用 |
| name / email / phone | varchar | phone、email 加密或脱敏存储 |
| password_hash | varchar | 接 OIDC 时可空 |
| external_subject | varchar UNIQUE | 企业微信/钉钉/OIDC subject |
| role | enum('employee','admin') | 本期两角色；预留 approver/finance |
| grade_code | varchar(32) | **职级码**（Q6=1，标准只按职级，这是最关键的字段） |
| dept_code | varchar(64) | 部门 |
| status | enum('active','disabled') | |
| last_login_at | timestamptz | |

索引：`(dept_code)`、`(grade_code)`、`(external_subject)`

### 3.2 sessions（会话）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK→users | |
| title | varchar(200) | 首轮自动生成，可改 |
| channel | enum('web','im') | |
| status | enum('active','archived') | |
| context_json | jsonb | 当前草稿 id、已填槽位、当前意图、制度版本过滤条件 |
| model / prompt_version | varchar | **记录当时用的模型与提示词版本，用于回溯** |
| message_count / token_total | int | |
| expires_at | timestamptz | 过期归档 |

索引：`(user_id, updated_at DESC)`

### 3.3 messages（消息）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| session_id | uuid FK→sessions | |
| role | enum('user','assistant','tool','system') | |
| content | text | |
| content_type | enum('text','markdown','card','event') | card 用于渲染草稿/票据卡片 |
| tool_name / tool_args / tool_result | varchar / jsonb | 工具调用留痕 |
| retrieval_refs | jsonb | **引用的制度条款：chunk_id、条款号、原文片段、相似度**（答案可溯源的依据） |
| guardrail_hits | jsonb | 命中的护栏（如"输出含金额承诺被拦截"） |
| model / prompt_tokens / completion_tokens | varchar / int | 成本归因 |
| latency_ms | int | |
| finish_reason / error_text | varchar / text | |
| parent_message_id | uuid | 支持重试与分支 |
| created_at | timestamptz | |

索引：`(session_id, created_at)`；`messages` 量大，二期按月分区。

### 3.4 policy_documents（报销制度文档）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| doc_code | varchar(64) | 制度编号，如 FIN-TRAVEL-2026 |
| version | varchar(20) | 版本号 |
| title | varchar(300) | |
| category | enum('travel_standard','invoice_rule','approval_flow','other') | |
| status | enum('draft','effective','deprecated') | **只有 effective 参与检索** |
| effective_from / effective_to | date | **生效区间，检索必过滤** |
| grade_scope | varchar[] | **适用职级范围，检索必过滤** |
| source_file_url / file_hash / mime_type | varchar | 原文留档，hash 防重复上传 |
| chunk_count / embedding_model / embedded_at | int / varchar / timestamptz | |
| uploaded_by | uuid FK→users | 管理员 |
| created_at / updated_at | timestamptz | |

约束：`UNIQUE(doc_code, version)`；索引：`(category, status, effective_from)`

### 3.5 policy_chunks（制度分片，承载向量）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| document_id | uuid FK→policy_documents | |
| clause_no | varchar(32) | **条款号，如 3.2.1 —— 引用的最小单位** |
| heading_path | varchar(500) | 章节路径，如 第三章>差旅>住宿 |
| chunk_index | int | |
| content | text | 分片原文 |
| token_count | int | |
| metadata | jsonb | category / grade_scope / effective_from-to / doc_version（检索过滤用） |
| embedding | vector(1024) | pgvector |

索引：`HNSW(embedding vector_cosine_ops)`、`GIN(metadata)`、`tsvector` 全文索引（混合检索）、`(document_id, chunk_index)`

### 3.6 reimbursement_drafts（报销单草稿）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| draft_no | varchar(32) UNIQUE | 人读单号 |
| user_id | uuid FK→users | |
| session_id | uuid FK→sessions NULL | 来源会话（智能体建的才有） |
| source | enum('agent','manual') | 区分智能体生成还是手填 |
| status | enum('draft','blocked','submitted','approved','rejected','withdrawn') | 一期到 approved |
| expense_type | enum('travel','entertain','office','other') | |
| period_start / period_end | date | |
| reason | text | |
| total_amount | numeric(14,2) | 由明细行汇总，冗余便于查询 |
| currency | char(3) DEFAULT 'CNY' | |
| grade_code_snapshot | varchar(32) | **提交时职级快照**，事后不受调职影响 |
| ruleset_version | varchar(32) | **规则版本快照**，审计可回放 |
| rule_result | jsonb | 命中规则 id / 阻断项 / 警告项 / 说明 |
| risk_level | enum('low','high') | Q10：高风险转人工 |
| budget_reservation_id | uuid NULL | 二期：占用号 |
| approval_instance_id | varchar NULL | 二期：OA 单号 |
| submitted_at / decided_at | timestamptz | |
| created_at / updated_at | timestamptz | |

索引：`(user_id, status, updated_at DESC)`、`(risk_level, status)`（复核队列）

### 3.7 辅助表（简表，支撑一期闭环）
- **draft_items（费用明细行）**：id / draft_id / expense_date / item_type / amount / invoice_id / note / rule_hits jsonb —— 明细独立成表，避免 JSONB 里做聚合查询。
- **invoices（票据）**：id / owner_id / invoice_no / invoice_code / amount / issue_date / seller / file_url / parse_result jsonb / parse_confidence numeric / status enum('unused','reserved','used','voided') / draft_id / `dedup_hash UNIQUE`（号码+代码+金额+日期四元组）。
- **audit_logs（审计）**：id / actor_id / action / target_type / target_id / before jsonb / after jsonb / request_id / trace_id / created_at —— **只追加，不更新不删除**。

---

## 4. 第一周最小验证目标（MVT）

### 目标定义（一句话）
> **一个员工用自然语言问制度问题 → 得到带条款引用的准确回答；再说一句话 → 智能体生成一张可查看、可被硬规则阻断的报销单草稿。**

端到端打通**一条链路**即可。目的是证伪"RAG + 工具调用"这两件最不确定的事，不是做一个能看的系统。

### 五天里程碑

| 天 | 交付物 | 验收标准 |
|---|---|---|
| D1 | 骨架运行 | docker-compose 起 pg+pgvector、redis、api；Alembic 建出 users/sessions/messages/policy_documents/policy_chunks/reimbursement_drafts；健康检查 200 |
| D2 | 制度入库 | 管理员上传 1 份《差旅报销标准》PDF → 解析 → **按条款切片** → 向量化 → 入库；后台能看到切片列表与条款号 |
| D3 | 问答闭环 | `/api/chat` SSE 流式；问"北京出差住宿标准多少"返回答案 + 条款号 + 原文片段；跑 20 条黄金问答，出准确率基线 |
| D4 | 填单闭环 | 3 个工具（search_policy / create_draft / add_item）；说"我昨天在北京出差住了两晚共 800" → 生成草稿，页面可见明细与合计 |
| D5 | 规则 + 观测 | 写死 2 条硬规则（住宿超标、发票重复）→ 提交时同步阻断并给条款依据；接入调用留痕，能看到 prompt、检索片段、工具链全链路 |

### 第一周明确不做
登录鉴权（mock 用户即可）、权限体系、预算占用、OA 集成、票据自动归集、前端美化（Swagger + 一个最简 HTML 足够）、移动端、支付凭证。
> 理由：这些都是确定性工作，晚做不影响可行性判断；**而 RAG 准确率和工具调用稳定性是唯一可能让项目失败的地方，必须最先证伪。**

### 提前定好的失败判据（避免自欺）
- 黄金问答准确率 < 50% → 切片策略有问题，第二周改为按条款/表格切，并给每片补全结构化元数据（适用职级、生效区间）。
- 工具调用成功率 < 80% → 收窄为"槽位填槽"式确定性流程（先问齐字段再建单），不要自由 ReAct。
- 平均首字延迟 > 3s → 检索层加缓存、rerank 降级、改用更小模型做意图分类。

### Week1 产出物
① 3 分钟可演示 demo（问制度 → 建单 → 触发阻断）；② 20 条问答的准确率基线表；③ 一份"哪些能做、哪些做不了"的结论，用来决定第二周是继续深化还是调整路线。
