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

# 测试
pytest tests/ -v
```

## 目录结构

```
venue-schedule-tool/
├── README.md          # 本文件
├── LICENSE            # MIT
├── .gitignore
├── CHANGELOG.md       # 版本记录
├── CONTRIBUTING.md    # 贡献指南
├── requirements.txt   # 依赖清单
├── src/venue_schedule_tool/
│   ├── main.py        # 程序入口（CLI 回放）
│   ├── core/          # 冲突检测 / 统一编号 / 复核留痕
│   └── utils/         # 样例数据读写
├── docs/              # 业务挖掘报告、行业分析、设计方案、报价单、流程图
├── tests/             # pytest 测试
├── examples/demo.py   # 30 样本回放 demo
├── scripts/           # 构建 / 部署脚本
├── config/            # 默认参数
├── data/              # 30 个档期样本 + 交接记录（合成演练数据）
└── .github/           # CI、Issue/PR 模板
```

## 验收口径（f02）

回放 30 个档期样本，识别全部已标注冲突且说明原因；每条提示附来源与人工复核记录。
`python examples/demo.py` 退出码 0 即通过。

## 许可证

MIT，见 [LICENSE](LICENSE)。数据与人名均为合成演练素材，不代表任何企业真实经营数据。
