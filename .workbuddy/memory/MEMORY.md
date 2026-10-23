# 项目长期记忆 · grade3_school_project

## 项目背景
大三课程作业：FDE（Forward Deployed Engineer）全流程训练，案例为**上海东浩会展经营有限公司「场馆排期与需求变更」数智化转型试点**（演练场景）。文件名 `grade3_school_project`，作业目录 `sb作业`。

## 核心约定（后续沿用）
- 企业名称与公开业务方向有公开来源，**人物、指标、业务问题均为演练虚构**，所有交付物须带此声明。
- 业务边界三条红线：①**只提示冲突，不自动锁档/签约/承诺容量**（f05/f52）；②正式结论须授权人员确认（f41）；③来源不清不得进入正式流转（f40）。
- 数据口径：占档状态三态（confirmed / unconfirmed / missing），**缺失值与未确认值必须区分**（f31）。
- 验收口径（f02）：回放 **30 个档期样本**，识别全部已标注冲突且说明原因，每条结果附来源与人工复核记录。
- 关键人物映射：顾明岚=业务负责人（范围决定权）；林亦舟=一线服务专员；唐映秋=项目流程协调员；秦芮=数字化工程师（数据与只读接口）；傅景=审核与质量负责人（**停止试点权**）；叶书=客户协同代表。

## 交付包结构
`venue-schedule-tool/`（按 GitHub `my-tool/` 框架）：src 主目录 + docs（4 份客户交付物 + 2 张流程图）+ tests + examples + scripts + config + data + .github。

## 工具链经验
- Word：走 `workbuddy-office-routing` → `workbuddy-docx`（S1 创作 → S2 美化 → S3 转换，present_files 成品预览）。
- Excel 新表：`workbuddy-sheet-generation`（openpyxl 直写；含公式时必须 recalc 成功，随后 aigc_label 打标）。
- 环境：受管 Python venv 位于 `C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe`；本机无 LibreOffice。
