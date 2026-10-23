# 贡献指南（CONTRIBUTING）

感谢参与「场馆档期冲突提示工具」。为保证业务边界不被破坏，提交前请阅读：

## 开发流程

1. 从 `main` 拉出功能分支：`git checkout -b feat/your-feature`
2. 本地跑通：`pytest tests/ -v` 与 `python examples/demo.py`（退出码必须为 0）
3. 提交 PR，模板见 `.github/PULL_REQUEST_TEMPLATE.md`，至少一名 reviewer 审核后合并

## 硬性业务红线（改动需单独评审）

- 任何“自动锁档 / 签约 / 承诺容量”能力一律不接收（f05/f52）
- 冲突提示必须携带原因与来源；去掉留痕的改动一律不接收（f02/f40）
- 缺失值与未确认值的区分语义不得合并（f31）

## 代码风格

- Python 3.10+，类型注解尽量齐全
- 测试与 `src` 结构对应，新增 `core` 功能必须带测试

## 问题反馈

提交 Issue 请使用 `.github/ISSUE_TEMPLATE/` 中对应模板，描述复现步骤与期望行为。
