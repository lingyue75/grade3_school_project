# 项目长期记忆 · grade3_school_project

## 项目背景
大三课程作业：FDE（Forward Deployed Engineer）全流程训练，案例为**上海东浩会展经营有限公司「场馆排期与需求变更」数智化转型试点**（演练场景）。文件名 `grade3_school_project`，作业目录 `sb作业`。

## 核心约定（后续沿用）
- 企业名称与公开业务方向有公开来源，**人物、指标、业务问题均为演练虚构**，所有交付物须带此声明。
- 业务边界三条红线：①**只提示冲突，不自动锁档/签约/承诺容量**（f05/f52）；②正式结论须授权人员确认（f41）；③来源不清不得进入正式流转（f40）。
- 数据口径：占档状态三态（confirmed / unconfirmed / missing），**缺失值与未确认值必须区分**（f31）。
- 验收口径（f02）：回放 **30 个档期样本**，识别全部已标注冲突且说明原因，每条结果附来源与人工复核记录。
- 关键人物映射：顾明岚=业务负责人（范围决定权）；林亦舟=一线服务专员；唐映秋=项目流程协调员；秦芮=数字化工程师（数据与只读接口）；傅景=审核与质量负责人（**停止试点权**）；叶书=客户协同代表。

## 交付包结构（2026-11-06 用户确认：顶层 docs/ 全部平铺）
- **★ 课程清单原图顶部写的是 `docs:`**：所有作业交付文档平铺在**仓库根 `docs/`**，无子目录。
  总索引 `docs/README.md`；`venue-schedule-tool/docs/` 及其 `project-brief/`、`test-results/` 子目录已删除，不要再建回去。
- 清单 1–8 项 + Test 三项全部在 `docs/`：六人访谈三格式、基本简报四件套、典型事件 txt+docx、
  行业分析、两张流程图、需求定义、设计方案、报价方案、测试方案、
  `(1)正常场景测试.docx` ～ `(5)故障场景测试.docx`、MVP 上线地址。
- 说明文件包 6 套 × json/md/csv 也在 `docs/` 平铺，索引为 `docs/说明文件包索引.md`。
- **★ Test 三项交付物单独放仓库根 `test/`**（21 文件 + `test/README.md` 索引，平铺无子目录）：
  测试方案、(1)~(5) 场景测试 docx/md/png、scenarios.json、执行汇总、MVP 上线地址文档。
  `docs/` 只放清单 1-8 项（39 文件）。`run_scenario_tests.py` 输出到 `test/`（`VENUE_TEST_DIR` 可覆盖）。
- `venue-schedule-tool/` 只放工程代码：src / tests / examples / scripts / config / data / site / .github。
  前 3 个生成脚本输出写入顶层 `docs/`（`VENUE_DOCS_DIR` 可覆盖）。
- **MVP 分双端**：`site/index.html` 客户端（开放，查档期/跑冲突检测）+ `site/admin.html` 管理端
  （只读统计/红线自检/冲突明细/复核状态）。`build_site.py` 同时生成两页，共用同一份数据与 detectAll 规则。
- 管理端有**用户名/密码登录界面**（演示账号见 `test/MVP上线部署地址.md`）：管理员可进、
  员工账号被拒、支持会话保持与退出。源码只存散列摘要。**必须如实标注为前端演示门禁，不是真实鉴权**——
  静态页面无服务端，页面源码与数据仍可查看；生产须服务端认证 + 接口级鉴权。
- 线上：https://venue-schedule-mvp.app.workbuddy.host/?sharecode=4KSZQPBMRT
  管理端 `/admin.html?sharecode=4KSZQPBMRT`；`workbuddy_sites_deploy` 重发同一目录即可更新，链接不变。
- 单一数据源约定不变：改内容改脚本重跑（`generate_briefs.py` 等），不手改单个格式文件；
  CSV 用 `utf-8-sig`；docx 由同名 md 经本地编辑器转换，场景文档需另行插图。

## ★ 主项目 vs 样例（重要，别搞混）
- **主项目 = `venue-schedule-tool/`（场馆排期与需求变更）**，所有正式工作围绕它。
- **`sample/expense-agent/`（报销助手）= 演练样例**：用同一套 10 步流水线（需求评审→架构→原型→接口→后台→Agent 设计→Agent 编排→评测→上线检查→结构推送）跑通的示范项目，已推送 GitHub，仅供流程参考，**不要再往里加新功能**。
- 现状：场馆项目已完成核心冲突检测（CLI + 30 样本回放 + pytest）与业务文档；**缺 Web/接口、管理后台、Agent、评测集、上线检查清单**。

## 工具链经验
- Word：走 `workbuddy-office-routing` → `workbuddy-docx`（S1 创作 → S2 美化 → S3 转换，present_files 成品预览）。
- Excel 新表：`workbuddy-sheet-generation`（openpyxl 直写；含公式时必须 recalc 成功，随后 aigc_label 打标）。
- 环境：受管 Python venv 位于 `C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe`；本机无 LibreOffice。
