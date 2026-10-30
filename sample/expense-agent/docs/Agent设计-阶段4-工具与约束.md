# 阶段4 · Agent 工具（Function Calling）与调用约束设计（待确认）

> 版本 v0.1 ｜ 2026-10-30 ｜ 状态：**待确认，确认后再实现**
> 目标：把"固定流程问答"升级为"会自主决策的智能体"，同时确保它**跳不过校验**。

---

## 0. 一个判断：这次升级最大的风险不是"不够聪明"，是"跳步"

固定流程里，校验是代码顺序写死的；一旦交给模型自主决策，`create_draft → submit_draft` 完全可以一步跳完。
因此本设计的第一原则是：

> **不变量必须下沉到代码，提示词只是第一道防线。**

四道防线（缺一不可）：

| 防线 | 位置 | 机制 | 强度 |
|---|---|---|---|
| ① 系统提示词 | 模型侧 | 硬约束条目 H1~H6 + 标准 SOP | 弱（可被绕过） |
| ② 工具返回即指令 | 工具层 | 被拒时返回"下一步该调哪个工具" | 中（模型通常会照做） |
| ③ 代码前置校验 + 校验令牌 | 服务端 | precondition + stamp 指纹 + 参数级拒绝 | **强（绕不过）** |
| ④ 工具动态可见性 | 编排层 | 未通过校验前，不把 `submit_draft` 放进 tools 列表 | **最强（看不见就调不了）** |

**结论：① 让模型"知道"，② 让模型"照做"，③④ 让模型"做不到"。**

---

## 1. Tools 还是 Skills？建议：底层用 Tools，外层包一个 Skill

| | Tools（Function Calling） | Skill（能力包） |
|---|---|---|
| 适合 | 强顺序、强约束、需要精确前置校验 | 松散、探索性、可复用的能力组合 |
| 报销场景 | ✅ 建单/加明细/提交必须串行且可校验 | ✅ "报销填单"整体作为一个技能包（SOP 提示词 + 工具子集 + 少样本） |

**建议形态**：底层 9 个 Tools（服务端强校验），外层一个 `reimbursement_filling` Skill（编排时加载：把该 Skill 的 SOP 注入系统提示词，并只暴露该阶段需要的工具子集）。这样既有 Agent 的自主性，又有流程的确定性。

---

## 2. 工具清单（9 个核心 + 3 个 P2）

### 2.1 只读类（无副作用，可并行调用）

| 工具 | 用途 | 返回 |
|---|---|---|
| `search_policy(query, category?)` | 检索制度条文（BM25+向量混合） | 带条款号与原文的片段列表 |
| `get_allowance(grade_code, city_tier, expense_type)` | 查职级报销标准 | 标准金额 + 条款号（**确定性查表，不走 LLM 推理**） |
| `list_invoices(status?, date_from?, date_to?)` | 列出票据池中可用票据 | 票据摘要列表 |
| `get_draft(draft_id)` | 查看草稿当前状态与明细 | 草稿快照 |

### 2.2 校验类（只读，但决定能否往下走）

| 工具 | 用途 | 返回 |
|---|---|---|
| `validate_invoice(invoice_ids[])` | 票据查重（号码+代码+金额+日期四元组）、状态校验（须 unused）、抬头税号校验 | `{result: pass\|blocked, blocked_ids[], reasons[]}` |
| `check_rules(draft_id)` | 对草稿跑规则引擎：超标、超标自担、缺附件、超期、重复票据 | `{result: pass\|blocked, violations[{rule_id, level, message, clause_no}]}` |

### 2.3 写入类（有副作用，**必须有前置**）

| 工具 | 用途 | 前置条件 |
|---|---|---|
| `create_draft(expense_type, period_start, period_end, reason?)` | 建草稿 | 同一用户无未提交的同类草稿（有则返回已有 id 让 Agent 复用） |
| `add_item(draft_id, invoice_ids[], item_type, amount?, date?, note?)` | 加明细行 | **① 该批票据的 `validate_invoice` 已通过（stamp 有效）② 票据状态为 unused** |
| `submit_draft(draft_id)` | 提交审批 | **① 至少 1 条明细 ② `check_rules` 已通过且 stamp 指纹未失效** |

### 2.4 P2（二期）

| 工具 | 用途 |
|---|---|
| `withdraw_draft(draft_id)` | 撤回未审完的单据 |
| `query_status(draft_no?)` | 查审批进度 |
| `ask_user(question, options?)` | 追问缺失槽位（中断式能力，见 §6） |

### 2.5 ★ 明确"不提供"的工具（能力边界即安全边界）

**不提供**：`delete_invoice`、`update_rule`、`override_rule`、`approve_draft`、`force_submit`、`modify_amount_beyond_standard`。
理由：Agent 能做什么是工具集决定的；把"绕过""强制""审批"这些能力从根上删掉，比事后拦更可靠。

---

## 3. 工具规范示例（Function Calling Schema 要点）

```json
{
  "name": "add_item",
  "description": "向报销单草稿添加一条费用明细。用于把已通过校验的票据挂到单据上。"
                "【前置】必须先对同一批 invoice_ids 调用 validate_invoice 且结果为 pass。"
                "【不要用】不要用它在未校验时挂票；不要用它修改已提交单据的明细。",
  "parameters": {
    "type": "object",
    "properties": {
      "draft_id":     { "type": "string", "description": "草稿 ID" },
      "invoice_ids":  { "type": "array", "items": {"type": "string"}, "minItems": 1 },
      "item_type":    { "type": "string", "enum": ["travel", "hotel", "meal", "transport", "entertain", "office", "other"] },
      "amount":       { "type": "number", "description": "金额（元）；不传则取票面金额" },
      "date":         { "type": "string", "format": "date" },
      "note":         { "type": "string", "maxLength": 200 },
      "client_request_id": { "type": "string", "description": "幂等键，重复调用安全" }
    },
    "required": ["draft_id", "invoice_ids", "item_type"]
  }
}
```

**Schema 设计三条经验**：
1. `description` 里必须写"**不要用**它做什么"——模型选错工具 80% 是因为描述只写了能做什么。
2. 参数尽量用 `enum` 收窄（item_type、city_tier），别让模型自由发挥字符串。
3. 统一返回 `{ok, code, message, data}`：`message` 是**给模型看的下一步指令**（见 §4 防线②）。

---

## 4. 调用顺序约束（**你特别关注的部分**）

### 4.1 状态机

```
NONE ──create_draft──▶ DRAFT ──add_item(需 invoice stamp)──▶ ITEMS_ADDED
      ──check_rules 通过(规则 stamp)──▶ VALIDATED ──submit_draft──▶ SUBMITTED
                                                          └─▶ APPROVED / REJECTED
```

### 4.2 顺序约束表

| 工具 | 允许状态 | 前置条件（服务端强制） | 被拒时返回 |
|---|---|---|---|
| `search_policy` / `get_allowance` / `list_invoices` / `get_draft` | 任意 | 无 | — |
| `create_draft` | NONE 或已有草稿 | 无未提交同类草稿 | `409 已存在草稿 xxx，请复用` |
| `validate_invoice` | 任意 | 票据归属当前用户 | `blocked + 具体原因` |
| `add_item` | DRAFT / ITEMS_ADDED | **invoice stamp 有效**：stamp 存在、未过期(5min)、`fingerprint == hash(sorted(invoice_ids))` | `400 {"message":"票据未通过校验，请先调用 validate_invoice(invoice_ids=[...])"}` |
| `check_rules` | 至少 1 条明细 | — | `violations[]` |
| `submit_draft` | VALIDATED | **rule stamp 有效**：stamp 存在、未过期、`fingerprint == hash(明细+金额+职级快照)`、无 blocking violation | `400 {"message":"规则校验未通过或内容已变更，请先调用 check_rules(draft_id)"}` |

### 4.3 ★ 关键机制：校验令牌（stamp）

```python
# 伪代码：不变量在服务端，模型无法伪造
def add_item(draft_id, invoice_ids, ...):
    fp = sha256(sorted(invoice_ids))
    stamp = get_stamp(draft_id, kind="invoice")
    if not stamp or stamp.fingerprint != fp or stamp.expired:
        raise ToolError(code=400, message="票据未通过校验，请先调用 validate_invoice(invoice_ids=[...])")
    ...
```

- `validate_invoice` / `check_rules` 通过后，服务端在草稿上写入带**内容指纹**的 stamp（有效期 5 分钟）。
- 任何改动（加明细、改金额）都会让指纹失效 → 必须重跑校验。
- 这就是"模型想跳也跳不过"的**代码级不变量**：即使提示词被绕过、模型强行调 `submit_draft`，服务端也只会返回错误，不会产生脏数据。

### 4.4 幂等与并发

- `add_item` / `submit_draft` 接受 `client_request_id`，重复调用返回首次结果（防模型重试造成重复明细）。
- 票据状态机 `unused → reserved → used`，加明细时原子占位，防两张单抢同一张票。
- 单用户同时只允许 1 个进行中的草稿（试点期），避免 Agent 建出一堆孤儿草稿。

---

## 5. 用系统提示词约束"不得跳过校验"（可直接用的文案骨架）

```markdown
# 角色
你是企业报销助手，帮助员工完成报销单填写。你只能使用列出的工具，不得臆造工具或参数。

# 硬约束（违反即为严重错误，H = Hard）
H1 顺序不可颠倒：validate_invoice（票据校验）通过前，禁止调用 add_item。
H2 check_rules（规则校验）通过前，禁止调用 submit_draft。
H3 任何一次对草稿的修改（新增明细、修改金额）之后，必须重新执行对应校验，
   之前的校验结果立即失效。
H4 校验返回 blocked 时，禁止重试绕过、禁止更换参数规避、禁止告知用户"已通过"。
   必须原样转述原因，并给出制度允许的替代路径（如超标自担、拆分说明、补附件）。
H5 不得自行解释或放宽报销标准；标准只能来自 get_allowance 或 search_policy 的返回。
H6 不得承诺结果：禁止说"一定能报销""审批会通过""我已经帮你提交成功了"，
   只能陈述系统返回的事实（如"已提交，单号 DR-2026-001，等待审批"）。

# 标准流程（SOP）
1. 理解需求 → 2. search_policy 确认制度依据 → 3. list_invoices 找可用票据
4. validate_invoice 校验票据 → 5. create_draft 建单 → 6. add_item 挂票
7. check_rules 校验规则 → 8. 向用户复述单据要点并【等待确认】→ 9. submit_draft

# 失败处理
- 工具被拒：按返回 message 的指示调用对应工具，不要猜测、不要换参数重试。
- 信息缺失：用 ask_user 追问，一次最多问 2 项，追问不超过 3 轮。
- 无法推进：说明卡在哪一步、缺什么，转人工。

# 停止条件
工具调用超过 8 步、或连续 2 次被拒，立即停止并总结当前状态交还用户。
```

**为什么这样写有效**：把约束编号（H1~H6）能让模型在推理时显式引用；把"禁止重试绕过"写死，能显著减少模型被拒后换参数硬闯的行为；"停止条件"防止无限循环。

**但请记住**：提示词只是防线①。真正兜底的是 §4.3 的 stamp（防线③）和 §4.4 的工具动态可见性（防线④）。

### 防线④ 的实现（最硬的一招）

编排器按状态动态裁剪 `tools` 数组：

```
草稿无 stamp_invoice → tools 中不含 add_item
草稿无 stamp_rule    → tools 中不含 submit_draft
```

模型看不到工具就调不了——这一招比任何提示词都可靠。

---

## 6. 槽位与追问

必填槽位：`expense_type` / `period_start,period_end` / 明细（票据 + 金额 + 日期）/ `reason`（部分类型必填）。
缺槽位 → `ask_user(question, options?)`，一次最多 2 项、累计不超过 3 轮；仍缺则转人工。
禁止模型替用户假设金额、日期或事由（H5 的延伸）。

---

## 7. 自主度分级（建议按此灰度，与 Q10 选择一致）

| 级别 | 能力 | 试点建议 |
|---|---|---|
| L0 | 只问答，不建单 | 已实现 |
| L1 | 建草稿、挂票，但**提交必须由用户点确认** | ✅ **试点期用 L1** |
| L2 | 低风险单据自动提交，高风险转人工 | 数据达标后开放 |
| L3 | 全自动含自动付款 | ❌ 不做 |

---

## 8. 观测与评测（上线前必须）

- 每次工具调用留痕：工具名、参数、返回码、耗时、当时 stamp 状态。
- 重点监控三个"作恶指标"：**跳过校验尝试率**（被 stamp 拦下的次数）、**被拒后换参数重试率**、**孤儿草稿数**。前两个指标高，说明提示词没写好。
- 评测集 `golden_fill.yaml`：≥ 20 条填单用例（含"票据重复""住宿超标""缺附件"等必须被拦的场景），要求：应拦的 100% 拦住，应通过的 100% 通过。

---

## 9. 需要你拍板的 8 点

| # | 决策 | 我的建议 |
|---|---|---|
| 1 | 底层 Tools + 外层 1 个 Skill，还是纯 Tools？ | **Tools 为底 + 1 个 `reimbursement_filling` Skill**（兼顾自主性与确定性） |
| 2 | 是否接受"校验令牌 stamp"机制？ | **必须**，这是唯一能代码级防跳步的手段 |
| 3 | stamp 有效期 | **5 分钟**（够长避免频繁重校验，够短防止内容变了还用旧结果） |
| 4 | 是否启用工具动态可见性（防线④）？ | **启用**，试点期只暴露到 `add_item`，`submit_draft` 由用户点按钮触发 |
| 5 | 试点自主度 | **L1**（Agent 建单，人提交），数据达标后开 L2 |
| 6 | 单用户是否只允许 1 个进行中草稿？ | **是**（试点期），避免孤儿草稿 |
| 7 | `ask_user` 追问上限 | 一次 2 项、累计 3 轮，超出转人工 |
| 8 | 是否要 `withdraw_draft`（撤回） | **P2**，一期不做（撤回涉及额度释放，与 Q4 的预算占用耦合） |

确认后我按 **① 工具 Schema 与服务端校验 → ② stamp 机制与状态机 → ③ Agent 编排器（LangGraph/自研）→ ④ 系统提示词与评测集** 四步实现，并补完上次中断的后台页面。
