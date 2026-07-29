"""追試 p3-200 の図を生成する。

配色は dataviz スキルの検証済みパレット。
逐次的な意味を持つ max_tokens は単一色相の濃淡（sequential）で表す。

使い方: uv run python 30_development/make_figures_cliff.py
"""

import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

import config

_cjk = None
for path in font_manager.findSystemFonts():
    if "DroidSansFallback" in path:
        font_manager.fontManager.addfont(path)
        _cjk = font_manager.FontProperties(fname=path).get_name()
        break
plt.rcParams["font.family"] = ["DejaVu Sans"] + ([_cjk] if _cjk else [])

# max_tokens は順序を持つ量なので単一色相の濃淡（青ランプ、薄→濃）。
# 隣接ペアの判別性を検証済み: 通常視 ΔE 15.3 / CVD 14.8（いずれも基準クリア）。
# 最も薄い #86bdf2 は面に対するコントラストが 3:1 未満のため、各線に直接ラベルを付けて補う。
RAMP = ["#86bdf2", "#2a78d6", "#1a4f8f", "#0d2847"]
ORANGE, BLUE = "#eb6834", "#2a78d6"
INK, MUTED, GRID = "#1a1a19", "#5c5b54", "#e4e3dd"
OUT = config.ROOT / "40_test" / "analysis" / "figures"
MTS = [2048, 4096, 8192, 16384]
LEVELS = [8, 16, 32, 48, 96]

plt.rcParams.update({
    "figure.dpi": 140, "savefig.dpi": 140, "axes.edgecolor": GRID,
    "axes.labelcolor": INK, "text.color": INK, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.axisbelow": True, "figure.facecolor": "white",
})


def load():
    sent, res = defaultdict(int), {}
    for line in (config.LOGS_DIR / "p3-200.jsonl").open():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["max_tokens"], r["format"], r["batch_size"], r["trial"], r["batch_index"])
        if r.get("event") == "sent":
            sent[k] += 1
        elif r.get("event") == "result":
            res[k] = r
    return sent, res


def style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def fig4_cliff_moves(res):
    """切断率 vs B を max_tokens 別に描く。崖が右へ動くのが見える図。"""
    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    xs = range(len(LEVELS))
    for mt, color in zip(MTS, RAMP):
        ys = []
        for b in LEVELS:
            rs = [res[k] for k in res if k[0] == mt and k[2] == b]
            ys.append(sum(1 for r in rs if r.get("finish_reason") == "length") / len(rs) * 100
                      if rs else 0)
        ax.plot(xs, ys, marker="o", markersize=7, linewidth=2, color=color,
                label=f"max_tokens = {mt:,}", zorder=3)
        # 右端に直接ラベル（凡例だけに頼らない）
        ax.annotate(f"{mt:,}", (len(LEVELS) - 1, ys[-1]), textcoords="offset points",
                    xytext=(8, -3), color=color, fontsize=9, fontweight="bold")
    ax.axhline(50, color=MUTED, linewidth=1, linestyle="--", zorder=2)
    ax.annotate("切断率50%（崖の定義）", (0, 52), fontsize=8.5, color=MUTED)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(LEVELS)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_ylim(-5, 108)
    ax.set_xlim(-0.25, len(LEVELS) - 0.5)
    ax.set_xlabel("バッチサイズ B（1リクエストあたりの件数）", fontsize=10)
    ax.set_ylabel("切断率（finish_reason = length）", fontsize=10)
    ax.legend(frameon=False, fontsize=9, loc="center left")
    style(ax)
    fig.suptitle("出力トークン予算を増やすと、崖は右へ動く", fontsize=13, y=0.98, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig4_cliff_moves.png", bbox_inches="tight")
    plt.close(fig)


def fig5_optimal_b(sent, res):
    """C(B) 曲線を max_tokens 別に。最良点が右へ移動するのが見える図。"""
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    xs = range(len(LEVELS))
    for mt, color in zip(MTS, RAMP):
        ys, pts = [], []
        for i, b in enumerate(LEVELS):
            keys = [k for k in res if k[0] == mt and k[2] == b and k[1] == "jsonl"]
            req = sum(sent[k] for k in keys)
            cor = sum(res[k].get("correct") or 0 for k in keys)
            if req and cor:
                ys.append(req / cor)
                pts.append(i)
            else:
                ys.append(None)
        valid = [(i, y) for i, y in zip(xs, ys) if y is not None]
        ax.plot([i for i, _ in valid], [y for _, y in valid], marker="o", markersize=7,
                linewidth=2, color=color, label=f"{mt:,}", zorder=3)
        # 最良点（C最小）を強調
        bi, bv = min(valid, key=lambda t: t[1])
        ax.scatter([bi], [bv], s=150, facecolors="none", edgecolors=color,
                   linewidths=2.2, zorder=4)
        ax.annotate(f"B={LEVELS[bi]}", (bi, bv), textcoords="offset points",
                    xytext=(4, -16), color=color, fontsize=9.5, fontweight="bold")
    ax.set_yscale("log")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(LEVELS)
    ax.set_xlabel("バッチサイズ B（1リクエストあたりの件数）", fontsize=10)
    ax.set_ylabel("実効コスト C(B)  リクエスト数 / 正解件数（対数）", fontsize=10)
    ax.set_xlim(-0.25, len(LEVELS) - 0.4)
    leg = ax.legend(frameon=False, fontsize=9, title="max_tokens", loc="lower left")
    leg.get_title().set_fontsize(9)
    style(ax)
    fig.suptitle("予算が増えるほど最適バッチサイズは大きくなる（○が各予算の最良点）",
                 fontsize=13, y=0.98, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig5_optimal_batchsize.png", bbox_inches="tight")
    plt.close(fig)


def fig6_failure_modes(res):
    """崩壊モード別の形式差。JSONLが効くのはモードBだけという精密化。"""
    trunc = [r for r in res.values() if r.get("finish_reason") == "length"]
    modes = [("モードA\nreasoning枯渇\n（本文が空）", [r for r in trunc if r.get("content_empty")]),
             ("モードB\n出力途中で切断", [r for r in trunc if not r.get("content_empty")])]
    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    width = 0.34
    for off, (fmt, color, name) in zip((-width / 2, width / 2),
                                       (("json_array", ORANGE, "JSON配列"),
                                        ("jsonl", BLUE, "JSONL"))):
        vals, notes = [], []
        for _, grp in modes:
            sub = [r for r in grp if r["format"] == fmt]
            s = sum(r["items_sent"] for r in sub)
            m = sum(r.get("id_matched") or 0 for r in sub)
            vals.append(m / s * 100 if s else 0)
            notes.append(f"{m}/{s}件")
        bars = ax.bar([i + off for i in range(len(modes))], vals, width=width,
                      color=color, label=name, zorder=3)
        for bar, v, note in zip(bars, vals, notes):
            ax.annotate(f"{v:.1f}%\n{note}", (bar.get_x() + bar.get_width() / 2, v),
                        textcoords="offset points", xytext=(0, 5), ha="center",
                        fontsize=9, color=INK, fontweight="bold")
    ax.set_xticks(range(len(modes)))
    ax.set_xticklabels([m[0] + f"\n{len(m[1])}件" for m in modes], fontsize=9.5)
    ax.set_ylim(0, 52)
    ax.set_yticks([0, 20, 40])
    ax.set_yticklabels(["0%", "20%", "40%"])
    ax.set_ylabel("アイテム回収率", fontsize=10)
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, fontsize=9)
    style(ax)
    fig.suptitle("JSONLの部分回収が効くのは「出力途中で切れた」ときだけ",
                 fontsize=13, y=0.99, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig6_failure_modes.png", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sent, res = load()
    fig4_cliff_moves(res)
    fig5_optimal_b(sent, res)
    fig6_failure_modes(res)
    for f in sorted(OUT.glob("fig[456]*.png")):
        print(f"{f.name}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
