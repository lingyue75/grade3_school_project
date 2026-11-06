# -*- coding: utf-8 -*-
"""
构建 MVP 静态演示站点：site/index.html（自包含单页，无构建、无后端、无外部依赖）

页面把 Python core 的三类冲突规则用原生 JS 复现，浏览器内即可跑完 30 样本回放：
  ① 区域时间窗重叠 + 装卸口进/撤场重叠（f11）
  ② 交接版本不一致（f20/f23）
  ③ 交接三项不齐套（f20/f23）
所有提示均为 advisory，页面不提供任何锁档 / 改期 / 提交能力（f05/f52）。

运行：python scripts/build_site.py
输出：site/index.html（供发布为在线链接）
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

out = SITE / "index.html"
out.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
print(f"已生成：{out}（{out.stat().st_size:,} 字节）")
print(f"内嵌数据：{len(norm_slots)} 条档期 / {len(norm_handovers)} 条交接")
