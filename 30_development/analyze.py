"""本実験 p3-100 の集計。仮説 H1'〜H6' を検証する。

集計規約（experiment スキル）:
- 消費は "sent" 行を数える
- 分析は "result" 行を使い、unit ごとに最後の成功 attempt のみ採用
- C(B) はプール比（総消費 req ÷ 総正解）

使い方: uv run python 30_development/analyze.py
"""

import json
from collections import defaultdict
from pathlib import Path

import config

LOG = config.LOGS_DIR / "p3-100.jsonl"
TASKS = ["label", "label_reason", "structured"]
LEVELS = [8, 16, 32, 48, 96]
FORMATS = ["json_array", "jsonl"]


def load():
    sent = defaultdict(int)
    results = {}
    for line in LOG.open():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r["task"], r["format"], r["batch_size"], r["trial"], r["batch_index"])
        if r.get("event") == "sent":
            sent[key] += 1
        elif r.get("event") == "result":
            results[key] = r          # 同一 unit は最後の行を採用
    return sent, results


def main() -> None:
    sent, results = load()
    print(f"# p3-100 集計（送信 {sum(sent.values())} req / result {len(results)} unit）\n")

    # --- H1' / H6': タスク×B×形式 の実効コストと回収率 ---
    print("## 実効コスト C(B) と回収率（H1', H6'）\n")
    print("| タスク | B | 形式 | 消費req | 正解 | 回収率 | C(B) | 切断 |")
    print("|---|---|---|---|---|---|---|---|")
    cb = {}
    for task in TASKS:
        for b in LEVELS:
            for fmt in FORMATS:
                keys = [k for k in results if k[0] == task and k[2] == b and k[1] == fmt]
                if not keys:
                    continue
                req = sum(sent[k] for k in keys)
                rs = [results[k] for k in keys]
                item_sent = sum(r["items_sent"] for r in rs)
                matched = sum(r.get("id_matched") or 0 for r in rs)
                correct = sum(r.get("correct") or 0 for r in rs)
                trunc = sum(1 for r in rs if r.get("finish_reason") == "length")
                c = req / correct if correct else float("inf")
                cb[(task, b, fmt)] = c
                cs = f"{c:.4f}" if correct else "∞"
                print(f"| {task} | {b} | {fmt} | {req} | {correct}/{item_sent} | "
                      f"{matched/item_sent:.1%} | {cs} | {trunc}/{len(rs)} |")

    # --- H3': 崖の位置とトークン数 ---
    print("\n## アイテムあたり出力トークンと崖の位置（H3'）\n")
    print("| タスク | B | 平均出力tok | tok/件 | 切断率 | content空 |")
    print("|---|---|---|---|---|---|")
    for task in TASKS:
        for b in LEVELS:
            rs = [results[k] for k in results if k[0] == task and k[2] == b]
            if not rs:
                continue
            ot = [r["output_tokens"] for r in rs if r.get("output_tokens")]
            avg = sum(ot) / len(ot) if ot else 0
            trunc = sum(1 for r in rs if r.get("finish_reason") == "length")
            empty = sum(1 for r in rs if r.get("content_empty"))
            print(f"| {task} | {b} | {avg:.0f} | {avg/b:.1f} | {trunc}/{len(rs)} | {empty} |")

    # --- H6': 切断が起きたリクエストに限定した形式別回収率（核心） ---
    print("\n## 切断時の形式別回収率（H6' の核心）\n")
    print("| 形式 | 切断req数 | 送信アイテム | 回収アイテム | 回収率 |")
    print("|---|---|---|---|---|")
    for fmt in FORMATS:
        rs = [results[k] for k in results
              if k[1] == fmt and results[k].get("finish_reason") == "length"]
        if not rs:
            print(f"| {fmt} | 0 | - | - | 切断なし |")
            continue
        s = sum(r["items_sent"] for r in rs)
        m = sum(r.get("id_matched") or 0 for r in rs)
        print(f"| {fmt} | {len(rs)} | {s} | {m} | **{m/s:.1%}** |")

    # --- H4': 切断時の欠落はバッチ後方に集中するか ---
    print("\n## 切断時の位置別回収率（H4'）\n")
    trunc = [results[k] for k in results if results[k].get("finish_reason") == "length"]
    if trunc:
        print("| 位置（四分位） | 送信 | 回収 | 回収率 |")
        print("|---|---|---|---|")
        buckets = defaultdict(lambda: [0, 0])
        for r in trunc:
            n = r["items_sent"]
            for p in r.get("per_item", []):
                q = min(3, p["pos"] * 4 // n)
                buckets[q][0] += 1
                buckets[q][1] += 1 if p.get("predicted") is not None else 0
        for q in sorted(buckets):
            s, m = buckets[q]
            print(f"| 第{q+1}四分位 | {s} | {m} | {m/s:.1%} |")
    else:
        print("切断が発生しなかったため検証不能。")

    # --- H5': レイテンシ ---
    print("\n## アイテムあたりレイテンシ（H5'）\n")
    print("| タスク | B | req当たり(ms) | アイテム当たり(ms) |")
    print("|---|---|---|---|")
    for task in TASKS:
        for b in LEVELS:
            rs = [results[k] for k in results if k[0] == task and k[2] == b]
            if not rs:
                continue
            lat = sum(r["latency_ms"] for r in rs) / len(rs)
            print(f"| {task} | {b} | {lat:.0f} | {lat/b:.1f} |")

    # --- 最適バッチサイズ ---
    print("\n## タスク別の最適バッチサイズ B*（形式別）\n")
    print("| タスク | 形式 | 最良B | C(B*) | B=8比の効率 |")
    print("|---|---|---|---|---|")
    for task in TASKS:
        for fmt in FORMATS:
            cand = {b: cb[(task, b, fmt)] for b in LEVELS if (task, b, fmt) in cb}
            if not cand:
                continue
            best = min(cand, key=lambda b: cand[b])
            base = cand.get(8, float("inf"))
            ratio = base / cand[best] if cand[best] else float("inf")
            print(f"| {task} | {fmt} | **{best}** | {cand[best]:.4f} | {ratio:.1f}倍 |")


if __name__ == "__main__":
    main()
