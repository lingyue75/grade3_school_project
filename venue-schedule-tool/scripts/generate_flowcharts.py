"""生成两张流程图 PNG：现状流程图、数智化转型后流程图。

依赖：matplotlib（pip install matplotlib）
运行：python scripts/generate_flowcharts.py
输出：docs/流程图-现状.png、docs/流程图-数智化转型后.png
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
OUT.mkdir(exist_ok=True)

# 配色（浅色主题）
C_START = "#DDEBF7"   # 起止
C_STEP = "#EAF3FB"    # 处理步骤
C_DECISION = "#FFF2CC"  # 判断点
C_PAIN = "#FDE9E9"    # 痛点/风险
C_DIGIT = "#E2EFDA"   # 数智化节点
C_TEXT = "#1F3864"
C_EDGE = "#7F9DBF"

BOX_W, BOX_H = 2.35, 0.85


def box(ax, x, y, text, color=C_STEP, w=BOX_W, h=BOX_H, fs=10.5, bold=False):
    ax.add_patch(FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.06", fc=color, ec="#5B84B1", lw=1.2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs,
            color=C_TEXT, fontweight="bold" if bold else "normal", linespacing=1.35)


def diamond(ax, x, y, text, w=2.5, h=1.05, fs=10):
    ax.add_patch(Polygon([(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)],
                         closed=True, fc=C_DECISION, ec="#C9A227", lw=1.2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, color=C_TEXT)


def arrow(ax, p1, p2, label=None, lx=0, ly=0, color=C_EDGE):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=14,
                                 color=color, lw=1.4, shrinkA=2, shrinkB=2))
    if label:
        ax.text((p1[0] + p2[0]) / 2 + lx, (p1[1] + p2[1]) / 2 + ly, label,
                fontsize=9, color="#8A4B08", ha="center")


def new_canvas(w, h, title, subtitle):
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    ax.set_xlim(0, 14)
    ax.set_ylim(0, h * 14 / w)
    ax.axis("off")
    ax.text(0.3, ax.get_ylim()[1] - 0.35, title, fontsize=16, fontweight="bold", color=C_TEXT)
    ax.text(0.3, ax.get_ylim()[1] - 0.75, subtitle, fontsize=10, color="#666666")
    return fig, ax


# ---------------------------------------------------------------- 现状流程图
def draw_current():
    fig, ax = new_canvas(12.5, 9.2, "图一 · 场馆档期申请现状流程（人工模式）",
                         "依据：业务需求发现报告 f01/f10/f11/f20/f23/f30/f31/f50/f52 —— 问题集中在交接点")

    top = ax.get_ylim()[1]
    y1 = top - 1.9     # 客户提出
    y2 = y1 - 1.5      # 意向询期
    y3 = y2 - 1.6      # 占档确认
    y4 = y3 - 1.6      # 合同审核
    y5 = y4 - 1.7      # 变更/冲突
    y6 = y5 - 1.5      # 判责
    y7 = y6 - 1.4      # 结果

    # 客户与销售
    box(ax, 3.0, y1, "客户提出档期诉求\n（邮件 / 电话）", C_START, bold=True)
    box(ax, 3.0, y2, "意向询期：销售受理\n排期表与邮件并行核对", C_STEP)
    box(ax, 7.6, y1, "变更诉求：改期 / 加场 /\n提前撤场（f50）", C_START, bold=True)
    box(ax, 7.6, y2, "排期表 vs 邮件需求\n人工逐项比对（f10）", C_PAIN)

    # 占档确认
    box(ax, 3.0, y3, "占档确认：跨三类工具\n（服务台/台账/文件库）\n人工串联，无统一编号（f30）", C_PAIN, w=3.0, h=1.15)
    box(ax, 7.6, y3, "关键字段口径不一\n区域/时间窗/进撤场时长\n缺失值与未确认值混判（f31）", C_PAIN, w=3.0, h=1.15)
    arrow(ax, (3.0, y2 - BOX_H / 2), (3.0, y3 + 0.62))
    arrow(ax, (7.6, y2 - BOX_H / 2), (7.6, y3 + 0.62))

    # 合同审核
    box(ax, 3.0, y4, "合同审核：来源不清不得流转\n正式结论须授权人确认（f40/f41）", C_STEP, w=3.0)
    box(ax, 7.6, y4, "交接只发「已处理」\n未列版本 / 未结项 / 截止时间\n（f20/f23）→ 下游重复核对", C_PAIN, w=3.0)
    arrow(ax, (3.0, y3 - 0.62), (3.0, y4 + BOX_H / 2))
    arrow(ax, (7.6, y3 - 0.62), (7.6, y4 + BOX_H / 2))

    # 冲突发现
    box(ax, 3.0, y5, "进撤场执行：\n临时变更靠人工逐项核对", C_STEP, w=3.0)
    diamond(ax, 8.2, y5, "是否发现冲突？\n（发现普遍偏晚）", w=3.4, h=1.3)
    arrow(ax, (3.0, y4 - BOX_H / 2), (3.0, y5 + BOX_H / 2))
    arrow(ax, (4.5, y5), (6.5, y5))

    # 判责
    box(ax, 3.4, y6, "翻原始邮件/台账版本\n人工判定责任节点（f11）", C_PAIN, w=3.2)
    arrow(ax, (6.9, y5 - 0.3), (4.6, y6 + 0.35), label="是", ly=0.12)
    box(ax, 8.2, y6, "未发现 → 冲突潜伏至执行期\n改场 / 加急成本", C_PAIN, w=3.2)
    arrow(ax, (9.4, y5 - 0.65), (8.6, y6 + 0.45), label="否", lx=0.35)

    box(ax, 5.8, y7, "重新排期 / 返工确认，重复核对循环", C_PAIN, w=3.4, bold=True)
    arrow(ax, (3.4, y6 - BOX_H / 2), (5.0, y7 + 0.3))
    arrow(ax, (8.2, y6 - BOX_H / 2), (6.8, y7 + 0.3))

    # 侧注：口径冲突
    box(ax, 12.0, y3, "销售希望延长\n客户使用时间\n↔ 运营保留\n恢复检查时间\n（f50 口径冲突）", C_PAIN, w=2.2, h=1.5, fs=9)
    arrow(ax, (10.85, y3), (10.9, y3), color="#FFFFFF")  # 占位

    ax.text(12.0, y1 - 0.2, "现状痛点", fontsize=11, fontweight="bold", color="#B23A3A", ha="center")

    fig.savefig(OUT / "流程图-现状.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ------------------------------------------------------- 数智化转型后流程图
def draw_future():
    fig, ax = new_canvas(12.5, 11.0, "图二 · 数智化转型后流程（冲突提示 + 人工确认）",
                         "边界（f05/f40/f52）：只提示冲突，不自动锁档/签约/承诺容量；正式结论由授权人员确认")

    y1 = 10.4   # 客户
    y2 = 9.15   # 统一编号
    y3 = 7.85   # 汇聚字段
    y4 = 6.30   # 齐套检查
    y5 = 4.95   # 三项齐套？
    y6 = 3.60   # 人工确认
    y7 = 2.45   # 来源完整？
    y8 = 1.10   # 审核/计划/台账

    # 主链路
    box(ax, 4.2, y1, "客户提出档期诉求\n（统一入口受理）", C_START, w=2.7, bold=True)
    box(ax, 4.2, y2, "系统自动分配统一关联编号\nDLG-YYYYMMDD-NNNN（f30）", C_DIGIT, w=3.2)
    box(ax, 4.2, y3, "自动汇聚关键字段：区域/时间窗/\n进撤场时长/版本/占档状态\n缺失值与未确认值分列（f31）", C_DIGIT, w=3.5, h=1.15)
    box(ax, 4.2, y4, "交接齐套检查（f20/f23）\n接收人 / 版本 / 未结项\n缺项自动拦截并提醒补充", C_DIGIT, w=3.4, h=1.15)
    box(ax, 4.2, y6, "人工确认（授权人员 f41）\n确认结论 + 复核记录留痕\n冲突不锁档，仅人工决策（f52）", C_DIGIT, w=3.6, h=1.15)

    arrow(ax, (4.2, y1 - BOX_H / 2), (4.2, y2 + BOX_H / 2))
    arrow(ax, (4.2, y2 - BOX_H / 2), (4.2, y3 + 0.62))
    arrow(ax, (4.2, y3 - 0.62), (4.2, y4 + 0.62))

    # 冲突引擎（右侧）
    box(ax, 9.6, y3, "冲突检测引擎（实时）\n① 区域时间窗重叠\n② 装卸口进/撤场重叠\n③ 交接版本不一致", C_DIGIT, w=3.3, h=1.35)
    arrow(ax, (5.97, y3), (7.93, y3), label="数据就绪", ly=0.15)
    box(ax, 9.6, y1, "提示推送：原因 + 来源\n+ 影响对象（f02/f40）", C_DIGIT, w=3.0, h=1.0, fs=9.5)
    arrow(ax, (9.6, y3 + 0.72), (9.6, y1 - 0.55), label="实时提示（不锁档）", lx=1.35)

    # 齐套判断
    diamond(ax, 4.2, y5, "三项齐套？", w=2.6, h=1.1)
    arrow(ax, (4.2, y4 - 0.62), (4.2, y5 + 0.58))
    box(ax, 1.1, y4, "拦截退回发起人\n列明版本/未结项\n/截止时间（f23）", C_PAIN, w=2.1, h=1.1, fs=9)
    arrow(ax, (2.9, y5 + 0.25), (1.6, y4 - 0.6), label="否", lx=-0.25, ly=0.1)
    arrow(ax, (1.35, y4 + 0.58), (3.05, y3 - 0.65), label="补齐后重检", lx=-0.55, ly=0.15)

    arrow(ax, (4.2, y5 - 0.58), (4.2, y6 + 0.62), label="是", lx=0.25)

    # 来源判断
    diamond(ax, 4.2, y7, "来源完整？", w=2.6, h=1.05)
    arrow(ax, (4.2, y6 - 0.62), (4.2, y7 + 0.56))
    box(ax, 9.6, y7, "来源不清：不得进入\n正式流转（f40）", C_PAIN, w=2.4, h=0.9, fs=9.5)
    arrow(ax, (5.52, y7), (8.38, y7), label="否", ly=0.15)

    # 末端
    box(ax, 2.3, y8, "合同审核：留痕引用\n授权人确认正式结论（f41）", C_STEP, w=3.0, fs=9.5)
    box(ax, 6.0, y8, "进撤场计划：装卸口顺序\n冲突提示辅助排程（f11）", C_STEP, w=3.0, fs=9.5)
    box(ax, 9.9, y8, "复核台账：全量留痕\n可回放审计（f02）", C_START, w=2.7, fs=9.5, bold=True)
    arrow(ax, (3.75, y7 - 0.55), (2.6, y8 + 0.48), label="是", lx=-0.3, ly=0.1)
    arrow(ax, (3.82, y8), (4.48, y8))
    arrow(ax, (7.52, y8), (8.53, y8))

    fig.savefig(OUT / "流程图-数智化转型后.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    draw_current()
    draw_future()
    print("已生成：", OUT / "流程图-现状.png")
    print("已生成：", OUT / "流程图-数智化转型后.png")
