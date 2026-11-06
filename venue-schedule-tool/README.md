# venue-schedule-tool · 场馆档期冲突提示工具（实验室系统）

> 上海东浩会展经营有限公司「场馆排期与需求变更」数智化转型试点 · 实验室系统
> 业务挖掘与需求发现见 `docs/`（业务挖掘报告、行业分析、设计方案、报价单）

## 项目是啥

针对档期申请链路「意向询期 → 占档确认 → 合同审核 → 进撤场计划」交接点的三个问题（重复核对、
无统一关联编号、冲突晚发现），提供**只提示、不锁档**的冲突检测工具：

- **统一关联编号**：`DLG-YYYYMMDD-NNNN`，串联服务台 / 业务台账 / 文件库三类工具；
- **冲突提示**：区域时间窗重叠、装卸口进/撤场重叠、交接版本不一致、交接三项不齐套；
- **齐套检查**：每次交接必须列明接收人、版本、未结项（缺一即提示）；
- **留痕与复核**：每条提示携带来源 + 人工复核记录，正式结论由授权人员确认。

## 业务边界（重要）

- 只提示排期冲突，**不自动锁档、不签约、不承诺容量**；
- 正式结论必须由授权人员确认；来源不清的提示不进入正式流转；
- 本系统为演练/实验室环境产物，**输出不得作为实际业务指令**。

## 怎么装

```bash
pip install -r requirements.txt   # 仅 pytest，核心逻辑零第三方依赖
pip install -e .                  # 或直接把 src 加入 PYTHONPATH
```

## 怎么跑

```bash
# 30 样本回放（对齐验收口径：识别全部已标注冲突并说明原因）
python examples/demo.py

# 命令行回放，输出 JSON 报告
PYTHONPATH=src python -m venue_schedule_tool.main --out report.json

# Agent 三条典型轨迹（离线可跑，含防跳步与锁档拦截验证）
python examples/agent_demo.py

# 测试
pytest tests/ -v
```

## Agent 编排（`src/venue_schedule_tool/agent/`）

在核心冲突检测之上叠加自主决策能力，**复用** `core/conflict.py` 而不重写：

| 模块 | 职责 |
|---|---|
| `tools.py` | 10 个工具 + 校验令牌 stamp + 工具动态可见性 + 禁用能力清单 |
| `planner.py` | 决策器：规则策略（离线）/ 真实 LLM Function Calling |
| `loop.py` | 执行循环：自主 5 轮 + **变更请求须授权人确认（中断/恢复）** |
| `store.py` | SQLite 持久化：运行实例 / 决策日志 / 变更请求 |

红线落地方式：工具集里**根本不提供** `lock_slot`/`sign_contract`/`commit_capacity`；
无来源（f40）不得发变更请求；`missing` 状态只报"数据不完整"（f31）；
正式结论只能由授权人员（傅景/顾明岚）作出（f41）。详见 `docs/Agent设计-工具与约束.md`。

## 项目说明文件（六人访谈 + 基本简报）

`docs/` 存放项目说明材料，**每套各有 json / md / csv 三种格式**：

| 说明文件 | 内容 | 三格式 |
| --- | --- | --- |
| 六人访谈记录 | 顾明岚、林亦舟、唐映秋、秦芮、傅景、叶书共 6 人、16 轮逐字稿 | `.json` `.md` `.csv` |
| 项目基本简报（总纲） | 速览、六条关键发现、三条红线、验收口径、文件索引 | `.json` `.md` `.csv` |
| 客户简报 | 背景、诉求、范围、验收、红线、治理、待补材料 | `.json` `.md` `.csv` |
| 现状信息请求表 | 36 条待补信息：用途、提供方、状态、优先级 | `.json` `.md` `.csv` |
| 业务发现访谈问题库 | 31 题，区分已问 / 待问 / 陷阱题 | `.json` `.md` `.csv` |
| 利益相关者资料与分工 | 六人资料、决策链、RACI、异常升级与停止条件 | `.json` `.md` `.csv` |

```bash
# 重新生成（单一数据源，改内容请改脚本后重跑，勿手改单个格式文件）
python scripts/generate_briefs.py
```

入口见 `docs/说明文件包索引.md`。所有文件自带演练虚构声明；CSV 为 `utf-8-sig` 编码。

## 今日完整流程 · 8 项交付包

按课程清单逐项交付，**对照表见 `docs/交付清单-今日完整流程.md`**：

| # | 交付物 | 关键文件 |
| --- | --- | --- |
| 1 | 六人访谈记录 | `docs/六人访谈记录.{md,json,csv}` |
| 2 | 项目基本简报四件套 | `项目基本简报-四件套.zip`（解压为 4 个 md）；完整三格式见 `docs/` |
| 3 | 典型事件访谈 | `典型事件访谈记录.txt`、`典型事件访谈整理.docx` |
| 4 | 行业分析 | `行业分析-会展场馆数智化转型.{md,docx}` |
| 5 | 改造前 / 后流程图 | `流程图-现状.png`、`流程图-数智化转型后.png`（融入典型事件） |
| 6 | 需求定义 | `需求定义文档.docx`（痛点 / AI 改造优先级 / 规格与追溯矩阵 / 验收清单） |
| 7 | 详细设计方案 | `设计方案-场馆档期冲突提示工具.{md,docx}` |
| 8 | 三档报价与投入产出 | `报价方案.docx`（低 / 中 / 高 + ROI 阈值测算） |

```bash
python scripts/generate_briefs.py       # 说明文件三格式
python scripts/generate_flowcharts.py   # 两张流程图
python scripts/generate_quote.py        # 报价方案 md
```

## 测试与上线

| 项 | 文件 / 地址 |
| --- | --- |
| 五类场景测试方案 | `docs/测试方案-五类场景.{md,docx}`（29 条用例、量化阈值、通过判据） |
| 可视化测试过程 | `docs/(1)~(5) 各场景测试.docx`（各含过程图） |
| 结构化实测结果 | `docs/scenarios.json`；5 张场景 PNG |
| 测试执行器 | `scripts/run_scenario_tests.py`（真实调用 src/，无 mock） |
| **MVP 上线地址** | **https://venue-schedule-mvp.app.workbuddy.host/?sharecode=4KSZQPBMRT** |
| 部署说明 | `docs/MVP上线部署地址.{md,docx}` |

```bash
python scripts/run_scenario_tests.py   # 重跑五类场景，重生成图与过程文档
python scripts/build_site.py           # 重建 site/index.html（改数据或规则后）
```

站点为静态 MVP 演示：浏览器内复现 `core/conflict.py` 的三类规则，
部署前已用 Node 与 Python 逐类比对（均 11 条、分布一致）。**不代表生产系统上线**。

## 目录结构

> **路径约定**：全部作业交付文档平铺在**仓库顶层 `docs/`**（与本目录 `venue-schedule-tool/` 平级），
> 不再有 `project-brief/`、`test-results/` 等子目录。下文与表格中的 `docs/xxx` 一律指仓库顶层 docs/。
> 总索引见 `docs/README.md`。

```
grade3_school_project/
├── docs/              # ★ 仓库顶层：全部作业交付文档（平铺，见 docs/README.md）
│                      #   清单 1-8 项 + Test 三项：六人访谈三格式、基本简报四件套、
│                      #   典型事件访谈、行业分析、两张流程图、需求定义、设计方案、
│                      #   报价方案、测试方案、(1)~(5) 场景测试 docx、MVP 上线地址
├── venue-schedule-tool/        # 工程代码（my-tool 框架）
│   ├── README.md              # 本文件
│   ├── CHANGELOG.md           # 版本记录
│   ├── src/venue_schedule_tool/
│   │   ├── main.py            # 程序入口（CLI 回放）
│   │   ├── core/              # 冲突检测 / 统一编号 / 复核留痕
│   │   ├── agent/             # Agent 工具与编排（只提示、不锁档）
│   │   └── utils/             # 样例数据读写
│   ├── site/index.html        # MVP 静态演示站点（已上线）
│   ├── tests/                 # pytest 测试
│   ├── examples/              # 30 样本回放 demo、Agent 三条轨迹
│   ├── scripts/               # 全部生成脚本（输出写入仓库顶层 docs/）
│   ├── config/                # 默认参数
│   ├── data/                  # 30 档期样本 + 交接记录（合成演练数据）
│   └── .github/               # CI、Issue/PR 模板
└── sample/expense-agent/      # 报销助手（演练样例，仅流程参考）
```

## 验收口径（f02）

回放 30 个档期样本，识别全部已标注冲突且说明原因；每条提示附来源与人工复核记录。
`python examples/demo.py` 退出码 0 即通过。

## 许可证

MIT，见 [LICENSE](LICENSE)。数据与人名均为合成演练素材，不代表任何企业真实经营数据。
