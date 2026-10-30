# 报销助手智能体系统（expense-agent）

企业级报销助手：制度知识库问答 + Agent 自主填单（带强校验与人工确认）+ 管理后台。
后端 FastAPI（异步）+ SQLite(pgvector 可替换) + FAISS 混合检索，前端服务端渲染 + SSE 流式。

> ⚠️ 本项目为**课程作业/原型演练**：企业制度文档、账号、票据、金额均为**虚构样例**，
> 全部集中在 `sample/` 目录，生产部署可整体删除。

---

## 1. 目录结构

```
expense-agent/
├── src/                    # 应用主代码
│   ├── main.py             # 装配入口（uvicorn src.main:app）
│   ├── settings.py         # 配置（★所有密钥/模型地址只在此出现）
│   ├── errors.py           # 统一错误码与中文错误信息
│   ├── security.py         # 密码哈希 / JWT / CSRF（纯标准库实现）
│   ├── db.py               # SQLite 数据访问（WAL、短事务）
│   ├── deps.py             # 登录态与接口级权限校验
│   ├── rag.py              # 检索索引（复用阶段1 的切片/向量/BM25）
│   ├── llm.py              # LLM 调用（mock / OpenAI 兼容，供应商可换）
│   ├── prompt_service.py   # 提示词模板（★版本化，发布即生效）
│   ├── service_chat.py     # 问答主流程（SSE 事件流 + 落库）
│   ├── api.py              # /api/v1/* JSON 与 SSE 接口
│   ├── agent_api.py        # /api/v1/agent/* Agent 接口
│   ├── admin_pages.py      # 服务端渲染管理后台
│   ├── agent/              # ★Agent 编排
│   │   ├── tools.py        #   工具 Schema + 服务端前置校验 + 校验令牌
│   │   ├── planner.py      #   决策器（规则策略 / LLM Function Calling）
│   │   ├── loop.py         #   执行循环（最多 5 轮 + 中断恢复）
│   │   └── schema.py       #   Agent 相关表与演示数据
│   └── web/                # 前端（templates + static）
├── sample/                 # ★演练样例（虚构制度文档、演示账号与票据）
├── scripts/                # 脚本：阶段1 原型、冒烟、评测、启动
├── eval/                   # 30 条评测用例与执行结果
├── tests/                  # 护栏回归测试（pytest）
├── docs/                   # 需求清单、架构设计、接口设计、Agent 设计、上线检查清单
├── data/                   # 运行时数据（数据库/索引/上传）—— 已 gitignore
└── .github/workflows/      # CI
```

## 2. 快速开始

```bash
pip install -r requirements.txt

# 离线可跑（内置模拟模型 + 哈希向量，无需任何 API Key）
python scripts/smoke_agent.py          # 跑 Agent 三条典型轨迹 + 防跳步验证
python scripts/run_eval.py             # 跑 30 条评测，输出 eval/results.md
python scripts/run_server.py           # 启动服务 → http://127.0.0.1:8000
```

演示账号：`admin / admin123`（管理员）、`1001 / 123456`（员工 P3）

接入真实模型（DeepSeek / 通义 / 豆包 / 本地 vLLM 均可，OpenAI 兼容协议）：

```bash
set LLM_PROVIDER=openai
set LLM_BASE_URL=https://api.deepseek.com/v1
set LLM_API_KEY=sk-xxx
set EMBED_BACKEND=api          # 需供应商支持 /embeddings；不支持时保持 hash 可跑通链路
```

## 3. 三个核心机制

| 机制 | 位置 | 作用 |
|---|---|---|
| **校验令牌 stamp** | `src/agent/tools.py` | 票据/规则校验通过后签发带内容指纹的令牌；内容一变即失效。**模型想跳过校验也绕不过**（代码级不变量） |
| **create_draft 人工确认中断** | `src/agent/loop.py` | 建单前强制中断，草稿预览流式发给员工，确认后才执行；状态持久化可恢复 |
| **提示词模板版本化** | `src/prompt_service.py` | 已发布版本不可改，只增版本；发布后缓存立即失效（下一问生效），历史问答可回放 |

防跳步四道防线：① 系统提示词硬约束 H1~H6 ② 工具被拒时返回"下一步该调什么" ③ stamp 前置校验 ④ 未校验前不暴露 `add_item`/`submit_draft`。

## 4. 质量保障

```bash
pytest tests/ -v        # 6 条护栏回归（未校验挂票/未校验提交/换票失效/令牌失效/工具可见性/标准查表）
python scripts/run_eval.py   # 30 条评测：10 制度（含 3 陷阱题）+ 10 工具调用 + 5 违规意图 + 5 多轮
```

评测说明：工具顺序可自动判定；**答案内容是否符合预期必须人工过目**（尤其陷阱题与违规意图题）。
当前规则策略器下工具顺序 22/30 通过，未通过项多为决策器能力边界，接入真实 Function Calling 后复测。

## 5. 设计文档（docs/）

| 文档 | 内容 |
|---|---|
| `需求清单-一页纸.md/.html` | 一页纸需求（目标/角色/决策快照/流程/验收/风险） |
| `架构设计-Python-FastAPI.md` | 四层架构、目录结构、表设计、第一周 MVT |
| `接口设计-阶段2-聊天入口.md` | SSE 协议、接口清单、上下文策略、错误码 |
| `接口设计-阶段3-管理后台.md` | 页面清单、接口清单、权限模型、表设计 |
| `Agent设计-阶段4-工具与约束.md` | 工具 Schema、顺序约束、stamp 机制、提示词硬约束 |
| `上线检查清单.md` | 5 维度 × 5 项，含负责人/验证方法/通过标准 |

## 6. 已知边界（原型阶段）

- **SQLite 适合原型**：并发写 > 50 需换 PostgreSQL（SQL 为通用写法，只需换驱动与占位符）。
- **FAISS 不支持真正删除**：文档删除为软删 + 检索过滤，后台提供"重建索引"全量重灌。
- **PDF 扫描件不支持**：无文本层的 PDF 会明确报错（不做 OCR）。
- **体验证阶段自主度为 L1**：Agent 可建单，提交必须人工点击（高风险动作不自动放行）。
- **未实现的 P2 工具**：`query_status`（查进度）、`withdraw_draft`（撤回，涉及额度释放）。
- **演示数据**：`sample/` 下的账号、票据、制度文档均为虚构，生产部署请删除该目录。

