"""記事用の図を生成する。

配色は dataviz スキルの検証済みカテゴリカルパレット（slot1 blue / slot2 orange）を使用。
`node scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a" --mode light` で全チェック通過を確認済み
（aqua のみ contrast WARN のため、該当図には直接ラベルを付けて可読性を担保する）。

使い方: uv run python 30_development/make_figures.py
"""

import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

import config

# 日本語表示。Droid Sans Fallback は CJK のみで Latin/数字を持たないため、
# DejaVu Sans を先頭に置いてグリフ単位のフォールバックを効かせる
_cjk = None
for path in font_manager.findSystemFonts():
    if "DroidSansFallback" in path:
        font_manager.fontManager.addfont(path)
        _cjk = font_manager.FontProperties(fname=path).get_name()
        break
# font.family にリストを直接渡すとグリフ単位のフォールバックが働く
# （font.sans-serif 経由では先頭フォントしか使われず日本語が豆腐になる）
plt.rcParams["font.family"] = ["DejaVu Sans"] + ([_cjk] if _cjk else [])

BLUE, ORANGE = "#2a78d6", "#eb6834"      # 検証済みパレット slot1 / slot2
INK, MUTED, GRID = "#1a1a19", "#5c5b54", "#e4e3dd"
OUT = config.ROOT / "40_test" / "analysis" / "figures"
LEVELS = [8, 16, 32, 48, 96]
TASKS = [("label", "ラベルのみ"), ("label_reason", "ラベル + 根拠"),
         ("structured", "ラベル + 根拠 + 確信度 + 観点")]

plt.rcParams.update({
    "figure.dpi": 140, "savefig.dpi": 140, "axes.edgecolor": GRID,
    "axes.labelcolor": INK, "text.color": INK, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.axisbelow": True, "figure.facecolor": "white",
})


def load():
    results = {}
    sent = defaultdict(int)
    for line in (config.LOGS_DIR / "p3-100.jsonl").open():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["task"], r["format"], r["batch_size"], r["trial"], r["batch_index"])
        if r.get("event") == "sent":
            sent[k] += 1
        elif r.get("event") == "result":
            results[k] = r
    return sent, results


def agg(results, task, fmt, b, field):
    rs = [results[k] for k in results if k[0] == task and k[1] == fmt and k[2] == b]
    sent_items = sum(r["items_sent"] for r in rs)
    got = sum(r.get(field) or 0 for r in rs)
    return got / sent_items if sent_items else 0.0


def style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def fig1_recovery(results):
    """アイテム回収率 vs B（タスク別の小倍数、形式で層別）。記事のヒーロー図。"""
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.9), sharey=True)
    for ax, (task, label) in zip(axes, TASKS):
        for fmt, color, name in (("json_array", ORANGE, "JSON配列"), ("jsonl", BLUE, "JSONL")):
            ys = [agg(results, task, fmt, b, "id_matched") * 100 for b in LEVELS]
            ax.plot(range(len(LEVELS)), ys, marker="o", markersize=7, linewidth=2,
                    color=color, label=name, zorder=3)
            # 直接ラベル（凡例だけに頼らない）
            ax.annotate(f"{ys[-1]:.0f}%", (len(LEVELS) - 1, ys[-1]),
                        textcoords="offset points", xytext=(6, -3),
                        color=color, fontsize=9, fontweight="bold")
        ax.set_xticks(range(len(LEVELS)))
        ax.set_xticklabels(LEVELS)
        ax.set_ylim(-6, 108)
        ax.set_title(label, fontsize=11, color=INK, pad=10)
        ax.set_xlabel("バッチサイズ B（1リクエストあたりの件数）", fontsize=9)
        style(ax)
    axes[0].set_ylabel("アイテム回収率", fontsize=10)
    axes[0].set_yticks([0, 25, 50, 75, 100])
    axes[0].set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    axes[0].legend(frameon=False, fontsize=9, loc="lower left")
    fig.suptitle("出力が軽ければ96件まとめても無傷。重くなると崖が現れ、JSON配列は全損する",
                 fontsize=12.5, y=1.02, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_recovery_by_batchsize.png", bbox_inches="tight")
    plt.close(fig)


def fig2_truncation(results):
    """切断リクエストからの形式別回収率。H6' の punchline。"""
    data = {}
    for fmt in ("json_array", "jsonl"):
        rs = [results[k] for k in results
              if k[1] == fmt and results[k].get("finish_reason") == "length"]
        s = sum(r["items_sent"] for r in rs)
        m = sum(r.get("id_matched") or 0 for r in rs)
        data[fmt] = (m / s * 100 if s else 0, len(rs), s, m)

    fig, ax = plt.subplots(figsize=(7.4, 2.3))
    names = ["JSON配列", "JSONL"]
    vals = [data["json_array"][0], data["jsonl"][0]]
    bars = ax.barh(names, vals, height=0.5, color=[ORANGE, BLUE], zorder=3)
    for bar, key, name in zip(bars, ("json_array", "jsonl"), names):
        pct, nreq, s, m = data[key]
        ax.annotate(f"{pct:.1f}%   （{m}/{s}件 · 切断{nreq}リクエスト）",
                    (max(pct, 0), bar.get_y() + bar.get_height() / 2),
                    textcoords="offset points", xytext=(8, 0), va="center",
                    fontsize=10, color=INK, fontweight="bold")
    ax.set_xlim(0, 46)
    ax.set_xticks([0, 10, 20, 30])
    ax.set_xticklabels(["0%", "10%", "20%", "30%"])
    ax.set_xlabel("切断されたリクエストからのアイテム回収率", fontsize=10)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    style(ax)
    fig.suptitle("出力が途中で切れたとき、JSON配列は1件も救えない", fontsize=12.5, y=1.03, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig2_truncation_recovery.png", bbox_inches="tight")
    plt.close(fig)


def fig3_position(results):
    """切断時の位置別回収率。H4'。"""
    buckets = defaultdict(lambda: [0, 0])
    for k, r in results.items():
        if r.get("finish_reason") != "length":
            continue
        n = r["items_sent"]
        for p in r.get("per_item", []):
            q = min(3, p["pos"] * 4 // n)
            buckets[q][0] += 1
            buckets[q][1] += 1 if p.get("predicted") is not None else 0

    qs = sorted(buckets)
    vals = [buckets[q][1] / buckets[q][0] * 100 for q in qs]
    labels = ["前方\n(1〜25%)", "(26〜50%)", "(51〜75%)", "後方\n(76〜100%)"]

    fig, ax = plt.subplots(figsize=(7, 3.6))
    bars = ax.bar(range(len(qs)), vals, width=0.55, color=BLUE, zorder=3)
    for bar, v in zip(bars, vals):
        ax.annotate(f"{v:.1f}%", (bar.get_x() + bar.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 5), ha="center",
                    fontsize=10, color=INK, fontweight="bold")
    ax.set_xticks(range(len(qs)))
    ax.set_xticklabels([labels[q] for q in qs], fontsize=9)
    ax.set_ylim(0, 26)
    ax.set_yticks([0, 10, 20])
    ax.set_yticklabels(["0%", "10%", "20%"])
    ax.set_ylabel("アイテム回収率", fontsize=10)
    ax.set_xlabel("バッチ内の位置", fontsize=10)
    ax.grid(axis="x", visible=False)
    style(ax)
    fig.suptitle("切断で失われるのはバッチ後方。前方は完成して残る", fontsize=12.5, y=1.02, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_position_recovery.png", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _, results = load()
    fig1_recovery(results)
    fig2_truncation(results)
    fig3_position(results)
    for f in sorted(OUT.glob("*.png")):
        print(f"{f.name}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
