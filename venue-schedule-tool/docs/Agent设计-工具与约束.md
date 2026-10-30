# Agent 工具与调用约束设计（场馆排期场景）

> 版本 v0.2 ｜ 2026-10-30 ｜ 实现见 `src/venue_schedule_tool/agent/`
> 与报销助手（演练样例）同构，但**红线不同**：样例是"不得跳过校验建单"，本项目是"只提示、不锁档"。

---

## 0. 四道防线（与样例一致）

| 防线 | 位置 | 机制 | 强度 |
|---|---|---|---|
| ① 系统提示词 | `planner.AGENT_SYSTEM_PROMPT` | 硬约束 H1~H6 + 标准 SOP | 弱（可被绕过） |
| ② 工具返回即指令 | `tools.py` 的 `message` | 被拒时直接写"下一步调什么" | 中（模型通常照做） |
| ③ 服务端校验令牌 stamp | `tools._stamp_valid` | 内容指纹 + 5 分钟有效期 | **强（绕不过）** |
| ④ 工具动态可见性 | `tools.visible_tools` | 未校验前不暴露写入类工具 | **最强（看不见就调不了）** |

**① 让模型"知道"，② 让模型"照做"，③④ 让模型"做不到"。**

---

## 1. 工具清单（10 个）

### 只读类
| 工具 | 用途 |
|---|---|
| `query_slots(zone?, dock?, date_from?, date_to?, status?)` | 查档期占用；**missing 记录单独报"数据不完整"（f31）** |
| `get_slot(app_id)` | 单个申请详情（区域/时间窗/版本/状态/来源） |
| `detect_conflict(app_id?, zone?)` | 冲突检测（区域重叠、装卸口重叠），只输出提示 |
| `check_handover(app_id)` | 交接三项齐套 + 版本一致性（f20/f23） |
| `validate_evidence(app_ids[])` | ★来源校验（f40），通过后签发 evidence 令牌 |
| `assess_change(app_id, new_window_*)` | 只读模拟：改期后冲突如何变化，**不落库** |

### 写入类（有前置）
| 工具 | 前置条件 |
|---|---|
| `draft_change_request(...)` | **evidence + conflict 两个 stamp 均有效**；调用后强制中断，交授权人确认 |
| `submit_handover(...)` | handover stamp 有效（check_handover 通过） |
| `request_authorization(app_id, reviewer)` | reviewer ∈ {傅景, 顾明岚}（f41） |
| `ask_user(question, options?)` | 缺信息时追问，累计 ≤3 轮 |

### ★ 永不提供的能力（能力边界即安全边界）
`lock_slot` / `confirm_booking` / `sign_contract` / `commit_capacity` / `override_conflict` / `delete_slot`
→ 定义在 `tools.FORBIDDEN_TOOLS`，即使被显式调用也返回 `5101`。**从根上删掉比事后拦截更可靠。**

---

## 2. 调用顺序约束

```
查询类任意 ──▶ detect_conflict ──▶ validate_evidence ──▶ assess_change
                     │                    │
                     └──── 两个 stamp 齐备 ──┘──▶ draft_change_request（★中断）
                                                        └─▶ 授权人确认 ─▶ request_authorization ─▶ final
check_handover ──▶ submit_handover
```

| 工具 | 前置（服务端强制） | 被拒时返回 |
|---|---|---|
| `draft_change_request` | evidence + conflict stamp 有效 | `4003 请先调用 validate_evidence(...) 与 detect_conflict(...)` |
| `submit_handover` | handover stamp 有效 | `4003 请先调用 check_handover(...)` |
| `request_authorization` | reviewer 为授权人员 | `5101 仅 傅景、顾明岚 有权作出正式结论` |

**校验令牌 stamp**：校验通过后签发 `fingerprint + 5 分钟有效期`；任何数据/范围变化都需重跑校验。
即使提示词被绕过、模型强行调用，服务端也只返回错误，**不会产生任何实际变更**。

---

## 3. 系统提示词硬约束（H1~H6，已写入代码）

> **H1** 只能提示，绝不锁档、签约、承诺容量；工具集中无此类能力，也不要声称能操作。
> **H2** 变更请求发出前必须中断，由授权人员（傅景/顾明岚）确认；正式结论由人作出（f41）。
> **H3** 来源不清（无服务台/台账/文件库记录号）不得进入正式流转（f40），必须拒绝并说明缺什么。
> **H4** 占档三态必须区分：`missing`（没填）**绝不等于没占档**，只能回答"数据不完整，无法判断"（f31）。
> **H5** 不得编造档期、版本、来源与结论；所有事实必须来自工具返回。
> **H6** 不得承诺结果：禁止说"档期已锁定""已为您保留"，只能陈述事实与"待授权人确认"。

停止条件：超过 5 轮 / 连续 2 次被拒 / 需要追问 → 停止并交还用户。

---

## 4. 三条典型轨迹（实测输出，见 `examples/agent_demo.py`）

**a. A厅 11-03~11-05 还有档期吗**（只读）
```
query_slots(6 条档期) → detect_conflict(2 条冲突提示) → final
✅ 已确认占用 4 条；2 条冲突提示均为 advisory；正式排期以授权人员确认结果为准
```

**b. 把 DLG-20261023-0001 延后两天**（含中断）
```
get_slot(v3/confirmed) → detect_conflict → validate_evidence(来源齐备)
→ assess_change(模拟 1→1) → ⏸中断：变更请求预览 → 傅景确认
→ draft_change_request(CR-…, 待授权) → request_authorization → final
✅ 状态"待授权人确认"；不得据此告知客户"档期已改"
```

**c. DLG-20261023-0002 的交接齐不齐**
```
check_handover → final ✅ 三项齐套且版本一致
```

**防跳步实测**：① 未校验直接发变更请求 → 4003 拦截 ② 调用 `lock_slot` → 5101 拒绝
③ 非授权人作结论 → 5101 ④ 未检查直接提交交接 → 4003 ⑤ 可见工具列表不含禁用能力 ⑥ missing 状态如实报"数据不完整"。

---

## 5. 与既有核心的关系

Agent **不重写**冲突检测，而是复用：`detect_slot_conflicts` / `detect_version_mismatch` / `detect_handover_gaps`（`core/conflict.py`）与 `utils/io.py` 的 CSV 读取。
新增持久化（SQLite，标准库）：`agent_runs` / `agent_step_logs` / `change_requests`。

> 已知待办：`Slot.effective_start/end` 目前直接返回 `window_start/end`，**未叠加 setup/teardown 时长**，
> 而冲突原因文案中声称"含进撤场时长口径（f31）"。两者口径需确认后统一（属核心模块改动，本次未擅自修改）。
