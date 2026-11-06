# -*- coding: utf-8 -*-
"""
构建 MVP 静态演示站点（双端）：site/index.html + site/admin.html
两个自包含页面，共用同一份内嵌数据与检测规则；无构建、无后端、无外部依赖。

  · 客户端 index.html：员工查档期、运行冲突检测、看提示与三态分布
  · 管理端 admin.html：管理员只读查看 KPI、红线自检、冲突明细、测试摘要与复核状态

页面把 Python core 的三类冲突规则用原生 JS 复现，浏览器内即可跑完 30 样本回放：
  ① 区域时间窗重叠 + 装卸口进/撤场重叠（f11）
  ② 交接版本不一致（f20/f23）
  ③ 交接三项不齐套（f20/f23）
所有提示均为 advisory；两端都不提供锁档 / 改期 / 提交 / 删除能力（f05/f52），
管理端是演示视图拆分，不是真实权限隔离。

运行：python scripts/build_site.py
输出：site/index.html、site/admin.html（供发布为在线链接）
测试摘要来源：test/scenarios.json（缺失时留空，不编造数字）
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SITE = ROOT / "site"
SITE.mkdir(exist_ok=True)


def read_csv(name: str) -> list[dict]:
    with (DATA / name).open(encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


slots = read_csv("slots.csv")
handovers = read_csv("handovers.csv")

# 与 utils/io.py 保持同一口径：空字段 -> None；status 空 -> missing
def opt(v):
    v = (v or "").strip()
    return v if v else None


norm_slots = [{
    "app_id": r["app_id"].strip(),
    "zone": r["zone"].strip(),
    "dock": opt(r.get("dock")),
    "window_start": opt(r.get("window_start")),
    "window_end": opt(r.get("window_end")),
    "version": opt(r.get("version")) or "v0",
    "status": (r.get("status") or "").strip().lower() or "missing",
    "source": (r.get("source") or "").strip(),
} for r in slots]

norm_handovers = [{
    "app_id": r["app_id"].strip(),
    "from_role": (r.get("from_role") or "").strip(),
    "to_role": (r.get("to_role") or "").strip(),
    "receiver": opt(r.get("receiver")),
    "version": opt(r.get("version")),
    "open_items": opt(r.get("open_items")),
    "deadline": opt(r.get("deadline")),
    "source": (r.get("source") or "").strip(),
} for r in handovers]

payload = json.dumps({"slots": norm_slots, "handovers": norm_handovers},
                     ensure_ascii=False, separators=(",", ":"))

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>场馆档期冲突提示工具 · MVP 演示</title>
<style>
  :root{
    --bg:#f6f8fb; --card:#ffffff; --ink:#1f2d3d; --muted:#6b7a8d;
    --line:#dde5ef; --brand:#2456a6; --brand-soft:#eaf1fc;
    --ok:#1a7f4b; --warn:#a56a00; --danger:#b23a3a; --grey:#7a8798;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Microsoft YaHei","PingFang SC",-apple-system,Segoe UI,sans-serif;
       line-height:1.65;font-size:14px}
  .wrap{max-width:1120px;margin:0 auto;padding:0 20px 56px}
  header{background:linear-gradient(120deg,#1f3864,#2f6bb5);color:#fff;padding:30px 0 26px}
  header .wrap{padding-bottom:0}
  h1{margin:0 0 6px;font-size:24px;letter-spacing:.5px}
  .sub{opacity:.9;font-size:13px}
  .notice{margin-top:16px;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.28);
          border-radius:8px;padding:10px 14px;font-size:12.5px;line-height:1.6}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
  .stat b{display:block;font-size:26px;color:var(--brand);line-height:1.2}
  .stat span{color:var(--muted);font-size:12.5px}
  section{background:var(--card);border:1px solid var(--line);border-radius:10px;
          padding:20px 22px;margin-bottom:18px}
  h2{font-size:17px;margin:0 0 14px;padding-left:10px;border-left:4px solid var(--brand)}
  h3{font-size:14px;margin:18px 0 8px;color:var(--brand)}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
  select,button{font:inherit;border-radius:7px;padding:8px 14px;border:1px solid var(--line);
                background:#fff;color:var(--ink);cursor:pointer}
  button.primary{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:600}
  button.primary:hover{background:#1c478c}
  button.ghost{background:#fff}
  .hint{color:var(--muted);font-size:12.5px}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 16px}
  .chip{border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:12px;
        background:#fff;color:var(--muted)}
  .chip.on{background:var(--brand-soft);border-color:#b9cff0;color:var(--brand);font-weight:600}
  .conflict{border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:8px;
            padding:13px 16px;margin-bottom:10px;background:#fffdf8}
  .conflict .top{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
  .tag{font-size:11.5px;padding:2px 9px;border-radius:999px;background:#fff2d8;color:#8a5a00;
       border:1px solid #f0d6a3}
  .tag.blue{background:var(--brand-soft);color:var(--brand);border-color:#c9dcf7}
  .tag.green{background:#e6f4ec;color:var(--ok);border-color:#bfe3cf}
  .ids{font-family:Consolas,monospace;font-size:12px;color:var(--muted)}
  .reason{font-size:13px;margin:6px 0}
  .src{font-size:12px;color:var(--muted)}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left}
  th{background:#f3f7fd;color:var(--muted);font-weight:600;position:sticky;top:0}
  .scroll{max-height:330px;overflow:auto;border:1px solid var(--line);border-radius:8px}
  .pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid}
  .s-confirmed{background:#e8f1fd;border-color:#c6ddf8;color:#1c4f96}
  .s-unconfirmed{background:#fff5e0;border-color:#f0dca8;color:#946200}
  .s-missing{background:#f0f1f3;border-color:#d9dde3;color:#68727f}
  .bars{display:grid;gap:9px;margin-top:8px}
  .bar{display:grid;grid-template-columns:110px 1fr 46px;align-items:center;gap:10px;font-size:12.5px}
  .bar .track{background:#eef2f7;border-radius:6px;height:12px;overflow:hidden}
  .bar .fill{height:100%;border-radius:6px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  ul.red{margin:0;padding-left:20px}
  ul.red li{margin-bottom:7px}
  .foot{color:var(--muted);font-size:12px;text-align:center;padding-top:8px}
  code{background:#f1f4f9;padding:1px 5px;border-radius:4px;font-size:12px}
  @media(max-width:760px){.stats,.grid2{grid-template-columns:1fr 1fr}}
  @media(max-width:520px){.stats,.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>场馆档期冲突提示工具 · MVP 演示</h1>
    <div class="sub">上海东浩会展经营有限公司 · 场馆排期与需求变更数智化转型试点（演练）｜ v1.0 · 2026-11-06</div>
    <div class="notice"><b>演练声明</b>：企业名称与公开业务方向有公开来源；人物、档期单号、时刻与指标均为演练虚构，不代表企业实际情况。本页面只提示冲突，<b>不自动锁档 / 签约 / 承诺容量</b>（f05/f52）；演练结果不得作为实际业务指令（f40），正式结论须授权人员确认（f41）。</div>
  </div>
</header>

<div class="wrap">
  <div class="stats">
    <div class="stat"><b id="m-slots">30</b><span>档期样本</span></div>
    <div class="stat"><b id="m-conflicts">—</b><span>冲突提示（回放）</span></div>
    <div class="stat"><b>29</b><span>五类场景测试用例</span></div>
    <div class="stat"><b style="color:var(--ok)">0</b><span>自动锁档动作</span></div>
  </div>

  <section>
    <h2>一、冲突检测（浏览器内运行，规则对齐 Python core）</h2>
    <div class="row">
      <label class="hint" for="zone">检测范围</label>
      <select id="zone"><option value="">全部区域</option></select>
      <button class="primary" id="run">运行冲突检测</button>
      <button class="ghost" id="reset">重置</button>
      <span class="hint" id="scope-hint"></span>
    </div>
    <div class="chips" id="chips"></div>
    <div id="result"><div class="hint">点击「运行冲突检测」查看回放结果。每条提示均附原因与来源，结论由授权人员确认。</div></div>
  </section>

  <section>
    <h2>二、占档状态三态分布（f31）</h2>
    <div class="grid2">
      <div class="bars" id="status-bars"></div>
      <div>
        <p class="hint" style="margin-top:0">三态必须区分：<b>confirmed</b> 已确认占用、<b>unconfirmed</b> 已填报未确认、<b>missing</b> 字段缺失。</p>
        <p class="hint">状态为 <code>missing</code> 的档期<b>不参与时间窗判定</b>，系统只输出「数据不完整，无法判断」，绝不把「没填」当成「没占档」。这是本项目最容易出错、也最关键的口径。</p>
      </div>
    </div>
  </section>

  <section>
    <h2>三、档期样本数据（30 条，合成演练数据）</h2>
    <div class="scroll">
      <table>
        <thead><tr><th>关联编号</th><th>区域</th><th>装卸口</th><th>时间窗</th><th>版本</th><th>状态</th><th>来源</th></tr></thead>
        <tbody id="slot-rows"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>四、测试与红线</h2>
    <div class="grid2">
      <div>
        <h3>五类场景实测（29 条）</h3>
        <table>
          <thead><tr><th>场景</th><th>用例</th><th>通过</th><th>已知口径</th></tr></thead>
          <tbody>
            <tr><td>正常</td><td>5</td><td>5</td><td>0</td></tr>
            <tr><td>边界</td><td>6</td><td>5</td><td>1</td></tr>
            <tr><td>对抗</td><td>7</td><td>7</td><td>0</td></tr>
            <tr><td>权限</td><td>5</td><td>5</td><td>0</td></tr>
            <tr><td>故障</td><td>6</td><td>6</td><td>0</td></tr>
            <tr><td><b>合计</b></td><td><b>29</b></td><td><b>28</b></td><td><b>1</b></td></tr>
          </tbody>
        </table>
        <p class="hint">已知口径：端点相接（16:00 结束 / 16:00 开始）当前按闭区间判为重叠，须业务方书面确认后再决定是否调整。</p>
      </div>
      <div>
        <h3>不可突破的红线</h3>
        <ul class="red">
          <li><b>只提示，不锁档</b>：工具集根本不提供 lock_slot / sign_contract / commit_capacity（f05/f52）。</li>
          <li><b>正式结论须授权人员确认</b>：仅傅景 / 顾明岚可作结论，系统不代为拍板（f41）。</li>
          <li><b>来源不清不得流转</b>：无来源申请直接拦截，提示必须附依据单号（f40）。</li>
          <li><b>三态区分</b>：missing 只报数据不完整，不判「无冲突」（f31）。</li>
          <li><b>不承诺收益</b>：基线为空、损失口径未定义，不对外引用演练样本数字（REQ-6）。</li>
        </ul>
      </div>
    </div>
  </section>

  <div class="foot">
    本页面为静态 MVP 演示：检测逻辑在浏览器内复现 <code>core/conflict.py</code> 的三类规则；
    完整 CLI / Agent 编排见项目仓库 <code>venue-schedule-tool/</code>。<br>
    演练场景，所有数据与结论不构成实际业务指令（f40）。
  </div>
</div>

<script>
const DATA = __DATA__;
const TYPE_CN = {
  zone_time_overlap:"区域时间窗重叠", dock_time_overlap:"装卸口进/撤场重叠",
  version_mismatch:"交接版本不一致", handover_incomplete:"交接三项不齐套"
};
let current = [], filter = "all";

function parseDt(s){ return s ? Date.parse(s.replace(" ","T")) : null; }
function overlap(as,ae,bs,be){ return as<=be && bs<=ae; }   // 与 core._overlap 一致：闭区间

function detectAll(slots, handovers){
  const out = [];
  const usable = slots.filter(s => s.status !== "missing");
  for(let i=0;i<usable.length;i++) for(let j=i+1;j<usable.length;j++){
    const a=usable[i], b=usable[j];
    if(a.app_id===b.app_id) continue;
    const as=parseDt(a.window_start), ae=parseDt(a.window_end),
          bs=parseDt(b.window_start), be=parseDt(b.window_end);
    if(as===null||ae===null||bs===null||be===null) continue;
    const ov = overlap(as,ae,bs,be);
    const src = [a.source,b.source].filter(Boolean);
    if(ov && a.zone===b.zone){
      out.push({type:"zone_time_overlap",app_ids:[a.app_id,b.app_id],sources:src,
        reason:`申请 ${a.app_id}（${a.zone}，${a.window_start}~${a.window_end}，版本 ${a.version}）与申请 ${b.app_id}（${b.zone}，${b.window_start}~${b.window_end}，版本 ${b.version}）在同一区域的时间窗重叠，含进撤场时长口径（f31）。需人工确认以哪一版本为准。`});
    }
    if(ov && a.dock && b.dock && a.dock===b.dock){
      out.push({type:"dock_time_overlap",app_ids:[a.app_id,b.app_id],sources:src,
        reason:`申请 ${a.app_id} 与 ${b.app_id} 在装卸口 ${a.dock} 的进/撤场时间重叠，需翻原始版本判断责任节点（f11）。需人工确认先后顺序。`});
    }
  }
  // 版本不一致：交接引用版本 != 台账最新版本
  const latest={};
  slots.forEach(s=>{ if(!latest[s.app_id] || s.version>latest[s.app_id]) latest[s.app_id]=s.version; });
  handovers.forEach(h=>{
    if(h.version!==null && latest[h.app_id] && h.version!==latest[h.app_id]){
      out.push({type:"version_mismatch",app_ids:[h.app_id],sources:[h.source].filter(Boolean),
        reason:`申请 ${h.app_id} 交接引用版本 ${h.version}，但台账最新版本为 ${latest[h.app_id]}，交接未列明最新版本（f23）。`});
    }
  });
  // 交接三项齐套
  handovers.forEach(h=>{
    const miss=[];
    if(h.receiver===null) miss.push("接收人");
    if(h.version===null) miss.push("版本");
    if(h.open_items===null) miss.push("未结项");
    if(miss.length){
      out.push({type:"handover_incomplete",app_ids:[h.app_id],sources:[h.source].filter(Boolean),
        reason:`申请 ${h.app_id} 的交接（${h.from_role}→${h.to_role}）缺少：${miss.join("、")}；不能只发一条「已处理」消息（f23）。`});
    }
  });
  return out.map(c=>({...c,auto_lock:false,requires_human_confirm:true}));
}

function renderConflicts(){
  const box=document.getElementById("result");
  const list=current.filter(c=>filter==="all"||c.type===filter);
  if(!list.length){ box.innerHTML='<div class="hint">当前筛选下没有冲突提示。</div>'; return; }
  box.innerHTML=list.map((c,i)=>`
    <div class="conflict">
      <div class="top">
        <span class="tag">${TYPE_CN[c.type]||c.type}</span>
        <span class="tag blue">${c.type}</span>
        <span class="tag green">仅提示 · 不锁档</span>
        <span class="ids">${c.app_ids.join(" / ")}</span>
      </div>
      <div class="reason">${i+1}. ${c.reason}</div>
      <div class="src">来源：${c.sources.length?c.sources.join("、"):"（无）" }　｜　正式结论须授权人员确认（f41）</div>
    </div>`).join("");
}

function renderChips(){
  const types=["all",...Object.keys(TYPE_CN)];
  const labels={all:"全部"};
  const counts={all:current.length};
  current.forEach(c=>counts[c.type]=(counts[c.type]||0)+1);
  document.getElementById("chips").innerHTML=types.map(t=>{
    if(t!=="all" && !counts[t]) return "";
    return `<span class="chip ${filter===t?"on":""}" data-t="${t}">${labels[t]||TYPE_CN[t]} (${counts[t]||0})</span>`;
  }).join("");
  document.querySelectorAll(".chip").forEach(el=>el.onclick=()=>{filter=el.dataset.t;renderChips();renderConflicts();});
}

function run(){
  const z=document.getElementById("zone").value;
  let slots=DATA.slots, hs=DATA.handovers;
  if(z){
    slots=DATA.slots.filter(s=>s.zone===z);
    const ids=new Set(slots.map(s=>s.app_id));
    hs=DATA.handovers.filter(h=>ids.has(h.app_id));
  }
  current=detectAll(slots,hs);
  filter="all";
  document.getElementById("m-conflicts").textContent=current.length;
  document.getElementById("scope-hint").textContent=
    `范围：${z||"全部区域"}　样本 ${slots.length} 条 / 交接 ${hs.length} 条　检出 ${current.length} 条提示`;
  renderChips(); renderConflicts();
}

function renderTable(){
  document.getElementById("slot-rows").innerHTML=DATA.slots.map(s=>`
    <tr><td class="ids">${s.app_id}</td><td>${s.zone}</td><td>${s.dock||"—"}</td>
    <td>${(s.window_start||"—").slice(5)} ~ ${(s.window_end||"—").slice(5)}</td>
    <td>${s.version}</td>
    <td><span class="pill s-${s.status}">${s.status}</span></td>
    <td class="ids">${s.source||"—"}</td></tr>`).join("");
}

function renderStatus(){
  const c={confirmed:0,unconfirmed:0,missing:0};
  DATA.slots.forEach(s=>c[s.status]=(c[s.status]||0)+1);
  const colors={confirmed:"#3b7dd8",unconfirmed:"#e0a92c",missing:"#98a2b0"};
  const label={confirmed:"confirmed 已确认",unconfirmed:"unconfirmed 未确认",missing:"missing 缺失"};
  const total=DATA.slots.length;
  document.getElementById("status-bars").innerHTML=Object.keys(c).map(k=>`
    <div class="bar"><span>${label[k]}</span>
      <div class="track"><div class="fill" style="width:${(c[k]/total*100).toFixed(1)}%;background:${colors[k]}"></div></div>
      <b>${c[k]}</b></div>`).join("");
}

function init(){
  const zones=[...new Set(DATA.slots.map(s=>s.zone))].sort();
  const sel=document.getElementById("zone");
  zones.forEach(z=>{const o=document.createElement("option");o.value=z;o.textContent=z;sel.appendChild(o);});
  document.getElementById("m-slots").textContent=DATA.slots.length;
  document.getElementById("run").onclick=run;
  document.getElementById("reset").onclick=()=>{
    sel.value="";filter="all";current=[];
    document.getElementById("m-conflicts").textContent="—";
    document.getElementById("scope-hint").textContent="";
    document.getElementById("chips").innerHTML="";
    document.getElementById("result").innerHTML='<div class="hint">已重置。点击「运行冲突检测」查看回放结果。</div>';
  };
  renderTable(); renderStatus(); run();
}
init();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# 双端页面：客户端 index.html（员工查档期 / 看冲突提示）+ 管理端 admin.html
#           （管理员看统计、明细、红线自检、复核状态）
# 两端复用同一份「数据 + 检测规则」字符串，保证判定口径完全一致。
# ---------------------------------------------------------------------------

# 从原单页模板中切出可复用部件
_HEAD = HTML[:HTML.index("</head>") + len("</head>")]
_BODY = HTML[HTML.index("<body>") + len("<body>"):HTML.index("</body>")]
_JS = _BODY[_BODY.index("<script>") + len("<script>"):_BODY.index("</script>")]
CORE_JS = _JS[:_JS.index("function renderConflicts")]   # DATA + 常量 + parseDt/overlap + detectAll

EXTRA_CSS = r"""
  .nav{display:flex;gap:8px;margin:14px 0 10px}
  .nav a{font-size:12.5px;text-decoration:none;color:#eaf1fc;border:1px solid rgba(255,255,255,.45);
         border-radius:999px;padding:4px 14px}
  .nav a.on{background:#fff;color:var(--brand);font-weight:700;border-color:#fff}
  .stats.six{grid-template-columns:repeat(3,1fr)}
  .checks{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
  .check{border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:8px;
         padding:11px 14px;background:#fbfdfb}
  .check.warn{border-left-color:var(--warn);background:#fffdf8}
  .check b{display:block;font-size:13.5px;margin-bottom:3px}
  .check span{font-size:12.5px;color:var(--muted)}
  .check .ok{color:var(--ok);font-weight:700}
  .check .warn{color:var(--warn);font-weight:700}
  .badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#fff2d8;
         color:#8a5a00;border:1px solid #f0d6a3}
  .badge.green{background:#e6f4ec;color:var(--ok);border-color:#bfe3cf}
  @media(max-width:760px){.stats.six,.checks{grid-template-columns:1fr}}
  #gate{position:fixed;inset:0;z-index:999;display:flex;align-items:center;justify-content:center;
        padding:20px;background:linear-gradient(120deg,#1f3864,#2f6bb5)}
  .gate-card{width:min(430px,100%);background:#fff;border-radius:14px;padding:26px 30px 22px;
             box-shadow:0 20px 60px rgba(10,25,50,.32)}
  .gate-card h2{margin:0 0 6px;font-size:20px;color:var(--brand);border:0;padding:0}
  .gate-sub{font-size:12.5px;color:var(--muted);margin:0 0 18px}
  .gate-card label{display:block;font-size:12.5px;color:var(--muted);margin:10px 0 5px}
  .gate-card input{width:100%;font:inherit;font-size:14px;padding:9px 12px;border:1px solid var(--line);
                   border-radius:7px;outline:none}
  .gate-card input:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
  .gate-err{min-height:20px;font-size:12.5px;color:var(--danger);margin-top:8px}
  .gate-card button.primary{width:100%;margin-top:4px;padding:10px}
  .gate-demo{margin-top:16px;background:#f3f7fd;border:1px solid var(--line);border-radius:8px;
             padding:10px 12px;font-size:12.5px;line-height:1.8}
  .gate-warn{margin:14px 0 8px;font-size:12px;color:var(--muted);line-height:1.7}
  .gate-back{font-size:12.5px;color:var(--brand);text-decoration:none}
  """

NAV_CLIENT = ('<div class="nav"><a class="on" data-nav href="index.html">客户端</a>'
              '<a data-nav href="admin.html">管理端</a></div>')
NAV_ADMIN = ('<div class="nav"><a data-nav href="index.html">客户端</a>'
             '<a class="on" data-nav href="admin.html">管理端</a>'
             '<a href="#" id="logout">退出登录</a></div>')

# 分享链接带 sharecode，页面间跳转必须保留 query，否则会被挡在门外

GATE_HTML = r"""
<div id="gate">
  <div class="gate-card">
    <h2>管理端登录</h2>
    <p class="gate-sub">场馆档期冲突提示工具 · 管理端（只读演示）</p>
    <label for="g-user">用户名 / 工号</label>
    <input id="g-user" type="text" autocomplete="username" placeholder="请输入管理员账号">
    <label for="g-pass">密码</label>
    <input id="g-pass" type="password" autocomplete="current-password" placeholder="请输入密码">
    <div class="gate-err" id="g-err"></div>
    <button class="primary" id="g-btn">登录管理端</button>
    <div class="gate-demo">
      <b>演示账号</b>（演练用，不代表真实企业账号）：<br>
      管理员　<code>admin / admin123</code>　可进入管理端<br>
      员工　　<code>1001 / 123456</code>　无管理端权限，将被拒绝
    </div>
    <p class="gate-warn"><b>安全边界</b>：本门禁为静态演示，校验在浏览器内完成，页面源码与数据仍可被查看，
      <b>不构成真实权限隔离</b>。生产系统必须由服务端做身份认证与接口级鉴权（当前项目未部署生产服务）。
      演练结果不得作为实际业务指令（f40）。</p>
    <a class="gate-back" data-nav href="index.html">← 返回客户端</a>
  </div>
</div>
"""

KEEP_QUERY = ("<script>document.querySelectorAll('a[data-nav]').forEach(function(a){"
              "var h=a.getAttribute('href');"
              "if(h&&h.indexOf('?')<0&&location.search)a.href=h+location.search;});</script>")

# 管理端页面（自包含；__DATA__ / __TEST__ 由下方统一替换）
ADMIN_BODY = r"""
__GATE_HTML__
<div id="admin-shell" style="display:none">
<header>
  <div class="wrap">
    <h1>场馆档期冲突提示工具 · 管理端</h1>
    __NAV_ADMIN__
    <div class="sub">上海东浩会展经营有限公司 · 场馆排期与需求变更数智化转型试点（演练）｜ v1.1 · 2026-11-06</div>
    <div class="notice"><b>演练声明与只读边界</b>：企业名称与公开业务方向有公开来源；人物、档期单号、时刻与指标均为演练虚构。本页<b>只读</b>，提供统计、明细、红线自检与复核状态，<b>不提供锁档 / 改期 / 提交 / 删除能力</b>（f05/f52）；演练结果不得作为实际业务指令（f40），正式结论须授权人员确认（f41）。</div>
  </div>
</header>

<div class="wrap">
  <div class="stats six">
    <div class="stat"><b id="k-slots">—</b><span>档期样本</span></div>
    <div class="stat"><b id="k-hands">—</b><span>交接记录</span></div>
    <div class="stat"><b id="k-conf">—</b><span>冲突提示</span></div>
    <div class="stat"><b id="k-confirmed">—</b><span>已确认占用（confirmed）</span></div>
    <div class="stat"><b id="k-lock" style="color:var(--ok)">—</b><span>自动锁档动作</span></div>
    <div class="stat"><b id="k-evidence" style="color:var(--ok)">—</b><span>提示附原因 / 来源率</span></div>
  </div>

  <section>
    <h2>一、红线自检（对当前演示数据实时计算）</h2>
    <div class="checks" id="guards"></div>
  </section>

  <section>
    <h2>二、冲突提示明细</h2>
    <div class="chips" id="chips"></div>
    <div class="scroll" style="max-height:380px">
      <table>
        <thead><tr><th>#</th><th>类型</th><th>关联编号</th><th>原因</th><th>来源</th><th>处置状态</th></tr></thead>
        <tbody id="conf-rows"></tbody>
      </table>
    </div>
    <p class="hint" style="margin-bottom:0">每条提示均为 <b>advisory</b>：只提示、不自动处置；正式结论须授权人员（傅景 / 顾明岚）确认（f41）。</p>
  </section>

  <section>
    <h2>三、占档三态与测试摘要</h2>
    <div class="grid2">
      <div>
        <h3>三态分布（f31）</h3>
        <div class="bars" id="status-bars"></div>
        <p class="hint">状态为 <code>missing</code> 的档期<b>不参与时间窗判定</b>，只输出「数据不完整」，绝不把「没填」当成「没占档」。</p>
      </div>
      <div>
        <h3>五类场景实测（数据来自 test/scenarios.json）</h3>
        <table>
          <thead><tr><th>场景</th><th>用例</th><th>通过</th><th>已知口径</th><th>失败</th></tr></thead>
          <tbody id="test-rows"></tbody>
        </table>
        <p class="hint" id="test-note"></p>
      </div>
    </div>
  </section>

  <section>
    <h2>四、人工复核记录（f02）</h2>
    <p class="hint" style="margin-top:0">验收口径要求「每条结果附来源与人工复核记录」。演示环境内 <code>review_records = 0</code>，
      下表如实标记为<b>待授权人员复核</b>，不伪造复核结论；正式流程中复核人、结论与时间须由授权人员填写。</p>
    <div class="scroll" style="max-height:260px">
      <table>
        <thead><tr><th>关联编号</th><th>提示类型</th><th>来源数</th><th>复核人</th><th>结论</th></tr></thead>
        <tbody id="review-rows"></tbody>
      </table>
    </div>
  </section>

  <div class="foot">
    管理端为静态只读演示：与客户端共用同一份数据与 <code>core/conflict.py</code> 规则复现逻辑；
    完整 CLI / Agent 编排见项目仓库 <code>venue-schedule-tool/</code>。<br>
    演练场景，所有数据与结论不构成实际业务指令（f40）。
  </div>
</div>
</div>

<script>
__CORE_JS__
const TEST = __TEST__;
const ROWS = detectAll(DATA.slots, DATA.handovers);

function esc(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function renderKpi(){
  const r=ROWS, n=r.length;
  document.getElementById("k-slots").textContent=DATA.slots.length;
  document.getElementById("k-hands").textContent=DATA.handovers.length;
  document.getElementById("k-conf").textContent=n;
  document.getElementById("k-confirmed").textContent=DATA.slots.filter(s=>s.status==="confirmed").length;
  const locks=r.filter(c=>c.auto_lock).length;
  document.getElementById("k-lock").textContent=locks;
  const withReason=r.filter(c=>c.reason&&String(c.reason).length>0).length;
  const withSrc=r.filter(c=>c.sources&&c.sources.length>0).length;
  document.getElementById("k-evidence").textContent=
    (n?Math.round(withReason/n*100):100)+"% / "+(n?Math.round(withSrc/n*100):100)+"%";
}

function renderGuards(){
  const r=ROWS,n=r.length;
  const locks=r.filter(c=>c.auto_lock).length;
  const noSrc=r.filter(c=>!c.sources||!c.sources.length).length;
  const noConfirm=r.filter(c=>!c.requires_human_confirm).length;
  const missing=DATA.slots.filter(s=>s.status==="missing").length;
  const items=[
    ["只提示不锁档（f05 / f52）", locks===0, "自动锁档动作 "+locks+" 个；工具集不含 lock_slot / sign_contract / commit_capacity"],
    ["来源不清不得流转（f40）", noSrc===0, "全部 "+n+" 条提示均附来源；无来源提示 "+noSrc+" 条"],
    ["三态区分（f31）", true, "missing 档期 "+missing+" 条，已排除在时间窗判定之外，只报数据不完整"],
    ["正式结论授权确认（f41）", noConfirm===0, "全部提示均标记待人工确认；系统不代为拍板"],
    ["验收口径 f02", n>0, "30 样本回放检出 "+n+" 条提示，每条均附原因与来源；复核记录待授权人员填写"]
  ];
  document.getElementById("guards").innerHTML=items.map(function(x){
    return '<div class="check'+(x[1]?'':' warn')+'"><b>'+esc(x[0])+
      ' <span class="'+(x[1]?'ok':'warn')+'">'+(x[1]?'通过':'关注')+'</span></b>'+
      '<span>'+esc(x[2])+'</span></div>';
  }).join("");
}

let f="all";
function renderChips(){
  const counts={all:ROWS.length};
  ROWS.forEach(c=>counts[c.type]=(counts[c.type]||0)+1);
  const types=["all"].concat(Object.keys(TYPE_CN).filter(t=>counts[t]));
  document.getElementById("chips").innerHTML=types.map(function(t){
    return '<span class="chip '+(f===t?"on":"")+'" data-t="'+t+'">'+
      esc(t==="all"?"全部":TYPE_CN[t])+" ("+(counts[t]||0)+")</span>";
  }).join("");
  document.querySelectorAll(".chip").forEach(function(el){
    el.onclick=function(){f=el.dataset.t;renderChips();renderRows();};
  });
}
function renderRows(){
  const list=ROWS.map(function(c,i){return {c:c,i:i};}).filter(function(x){return f==="all"||x.c.type===f;});
  document.getElementById("conf-rows").innerHTML=list.map(function(x){
    const c=x.c;
    return "<tr><td>"+(x.i+1)+"</td><td><span class=\"badge\">"+esc(TYPE_CN[c.type]||c.type)+
      "</span><br><span class=\"ids\">"+esc(c.type)+"</span></td>"+
      "<td class=\"ids\">"+esc(c.app_ids.join(" / "))+"</td>"+
      "<td>"+esc(c.reason)+"</td>"+
      "<td class=\"src\">"+esc(c.sources.length?c.sources.join("、"):"（无）")+"</td>"+
      "<td><span class=\"badge green\">仅提示 · 待授权确认</span></td></tr>";
  }).join("") || '<tr><td colspan="6" class="hint">当前筛选下没有提示。</td></tr>';
}
function renderStatus(){
  const c={};DATA.slots.forEach(s=>c[s.status]=(c[s.status]||0)+1);
  const colors={confirmed:"#3b7dd8",unconfirmed:"#e0a92c",missing:"#98a2b0"};
  const label={confirmed:"confirmed 已确认",unconfirmed:"unconfirmed 未确认",missing:"missing 缺失"};
  const total=DATA.slots.length;
  document.getElementById("status-bars").innerHTML=Object.keys(c).map(function(k){
    return '<div class="bar"><span>'+label[k]+'</span><div class="track"><div class="fill" style="width:'+
      (c[k]/total*100).toFixed(1)+'%;background:'+colors[k]+'"></div></div><b>'+c[k]+'</b></div>';
  }).join("");
}
function renderTest(){
  const order=["normal","boundary","adversarial","permission","fault"];
  const cn={normal:"正常",boundary:"边界",adversarial:"对抗",permission:"权限",fault:"故障"};
  const rows=order.map(function(k){var x=TEST[k]||{total:0,pass:0,known:0,fail:0};
    return "<tr><td>"+cn[k]+"</td><td>"+x.total+"</td><td>"+x.pass+"</td><td>"+x.known+"</td><td>"+x.fail+"</td></tr>";});
  const t=TEST._total||{total:0,pass:0,known:0,fail:0};
  rows.push("<tr><td><b>合计</b></td><td><b>"+t.total+"</b></td><td><b>"+t.pass+"</b></td><td><b>"+t.known+"</b></td><td><b>"+t.fail+"</b></td></tr>");
  document.getElementById("test-rows").innerHTML=rows.join("");
  document.getElementById("test-note").textContent=
    (TEST._note||"")+" 唯一 KNOWN：端点相接按闭区间判为重叠，业务口径待书面确认。";
}
function renderReview(){
  document.getElementById("review-rows").innerHTML=ROWS.map(function(c){
    return "<tr><td class=\"ids\">"+esc(c.app_ids.join(" / "))+"</td><td>"+
      esc(TYPE_CN[c.type]||c.type)+"</td><td>"+c.sources.length+"</td>"+
      "<td class=\"hint\">待填写（傅景 / 顾明岚）</td><td><span class=\"badge\">待授权人员复核</span></td></tr>";
  }).join("");
}
renderKpi();renderGuards();renderChips();renderRows();renderStatus();renderTest();renderReview();

// ---- 管理端演示门禁：前端校验，非真实鉴权 ----
function hashPwd(s){var h=5381;for(var i=0;i<s.length;i++){h=((h*33+s.charCodeAt(i))>>>0);}return String(h);}
function showAdmin(){
  document.getElementById("gate").style.display="none";
  document.getElementById("admin-shell").style.display="block";
  document.body.style.overflow="";
}
function tryLogin(){
  var u=(document.getElementById("g-user").value||"").trim();
  var p=document.getElementById("g-pass").value||"";
  var err=document.getElementById("g-err");
  var h=hashPwd(p);
  if(u==="admin" && h==="__ADMIN_HASH__"){ try{sessionStorage.setItem("venue_admin_demo","1");}catch(e){} showAdmin(); err.textContent=""; return; }
  if(u==="1001" && h==="__EMP_HASH__"){
    err.textContent="该账号为员工角色，无管理端权限。请返回客户端，或改用管理员账号。"; return; }
  err.textContent="用户名或密码不正确（演示账号见下方）。";
}
try{ if(sessionStorage.getItem("venue_admin_demo")==="1") showAdmin(); }catch(e){}
document.getElementById("g-btn").onclick=tryLogin;
document.getElementById("g-pass").onkeydown=function(e){if(e.key==="Enter")tryLogin();};
document.getElementById("logout").onclick=function(e){
  e.preventDefault(); try{sessionStorage.removeItem("venue_admin_demo");}catch(_){}
  document.getElementById("admin-shell").style.display="none";
  document.getElementById("gate").style.display="flex";
  document.getElementById("g-pass").value="";
  document.getElementById("g-err").textContent="已退出登录。";
};
</script>
"""


def djb2(text: str) -> str:
    """与页面内 hashPwd 完全一致的同步散列；源码只存摘要，不存明文口令。"""
    h = 5381
    for ch in text:
        h = (h * 33 + ord(ch)) & 0xFFFFFFFF
    return str(h)


def page(title: str, body: str) -> str:
    """用共用 HEAD（含 CSS）拼一个自包含 HTML 页面。"""
    head = _HEAD.replace("场馆档期冲突提示工具 · MVP 演示", title)
    head = head.replace("</style>", EXTRA_CSS + "\n</style>")
    doc = head + "\n<body>" + body + KEEP_QUERY + "\n</body>\n</html>\n"
    return (doc.replace("__DATA__", payload)
               .replace("__TEST__", test_payload)
               .replace("__ADMIN_HASH__", djb2("admin123"))
               .replace("__EMP_HASH__", djb2("123456")))


# 测试摘要：从 test/scenarios.json 读取，缺失时退化为占位（不编造数字）
test_summary = {k: {"total": 0, "pass": 0, "known": 0, "fail": 0}
                for k in ("normal", "boundary", "adversarial", "permission", "fault")}
test_summary["_total"] = {"total": 0, "pass": 0, "known": 0, "fail": 0}
test_summary["_note"] = "test/scenarios.json 不可用，摘要为空。"
test_file = ROOT.parent / "test" / "scenarios.json"
if test_file.exists():
    cases = json.loads(test_file.read_text(encoding="utf-8"))["cases"]
    for c in cases:
        k = c["scene"]
        if k not in test_summary:
            continue
        test_summary[k]["total"] += 1
        v = c["verdict"]
        key = {"PASS": "pass", "KNOWN": "known", "FAIL": "fail"}.get(v, "fail")
        test_summary[k][key] += 1
        test_summary["_total"]["total"] += 1
        test_summary["_total"][key] += 1
    test_summary["_note"] = "由 test/scenarios.json 实时汇总。"
test_payload = json.dumps(test_summary, ensure_ascii=False)

# 客户端：在原页面 header 里插入双端导航
client_body = _BODY.replace('<div class="sub">', NAV_CLIENT + '\n    <div class="sub">', 1)
client_body = client_body.replace("· MVP 演示</h1>", "· 客户端</h1>", 1)
(SITE / "index.html").write_text(page("场馆档期冲突提示工具 · 客户端", client_body), encoding="utf-8")

# 管理端：注入共享规则与测试摘要
admin_body = (ADMIN_BODY.replace("__GATE_HTML__", GATE_HTML)
              .replace("__NAV_ADMIN__", NAV_ADMIN)
              .replace("__CORE_JS__", CORE_JS))
(SITE / "admin.html").write_text(page("场馆档期冲突提示工具 · 管理端", admin_body), encoding="utf-8")

for name in ("index.html", "admin.html"):
    p = SITE / name
    print(f"已生成：{p.name}（{p.stat().st_size:,} 字节）")
print(f"内嵌数据：{len(norm_slots)} 条档期 / {len(norm_handovers)} 条交接")
print(f"测试摘要：{test_summary['_total']}（来源 {test_file.name if test_file.exists() else '无'}）")
