"""追試 p3-200 の集計: max_tokens で崖は動くか。

崖の位置を「切断率が50%を超える最小の B」と定義し、予測式
    B* ≈ max_tokens ÷ アイテムあたり出力トークン
と突き合わせる。

使い方: uv run python 30_development/analyze_cliff.py
"""

import json
from collections import defaultdict

import config

LOG = config.LOGS_DIR / "p3-200.jsonl"
MTS = [2048, 4096, 8192, 16384]
LEVELS = [8, 16, 32, 48, 96]
FORMATS = ["json_array", "jsonl"]


def load():
    sent, results = defaultdict(int), {}
    for line in LOG.open():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["max_tokens"], r["format"], r["batch_size"], r["trial"], r["batch_index"])
        if r.get("event") == "sent":
            sent[k] += 1
        elif r.get("event") == "result":
            results[k] = r
    return sent, results


def cell(results, mt, b, fmt=None):
    return [results[k] for k in results
            if k[0] == mt and k[2] == b and (fmt is None or k[1] == fmt)]


def main() -> None:
    sent, results = load()
    print(f"# p3-200 集計（送信 {sum(sent.values())} req / result {len(results)} unit）\n")

    # --- 崖の移動: 切断率のマトリクス ---
    print("## 切断率マトリクス（finish_reason=length の割合）\n")
    header = " | ".join(f"B={b}" for b in LEVELS)
    print(f"| max_tokens | {header} | 崖の位置（実測） | 予測 |")
    print("|---" * (len(LEVELS) + 3) + "|")
    observed = {}
    for mt in MTS:
        cells, cliff = [], None
        for b in LEVELS:
            rs = cell(results, mt, b)
            if not rs:
                cells.append("-")
                continue
            rate = sum(1 for r in rs if r.get("finish_reason") == "length") / len(rs)
            cells.append(f"{rate:.0%}")
            if cliff is None and rate >= 0.5:
                cliff = b
        observed[mt] = cliff
        # 予測: 切断が起きなかったセルの実測 tok/件 から算出
        clean = [r for b in LEVELS for r in cell(results, mt, b)
                 if r.get("finish_reason") == "stop" and r.get("output_tokens")]
        tpi = (sum(r["output_tokens"] / r["items_sent"] for r in clean) / len(clean)
               if clean else None)
        pred = f"B≈{mt / tpi:.0f}" if tpi else "算出不可"
        print(f"| {mt} | {' | '.join(cells)} | "
              f"{'B=' + str(cliff) if cliff else '範囲内になし'} | {pred} |")

    # --- 崖が動いたか ---
    print("\n## 崖の移動\n")
    print("| max_tokens | 崖（切断率50%超の最小B） | 前水準比 |")
    print("|---|---|---|")
    prev = None
    for mt in MTS:
        c = observed.get(mt)
        ratio = f"{c / prev:.1f}倍" if (c and prev) else "-"
        print(f"| {mt} | {'B=' + str(c) if c else '範囲内になし（B>96）'} | {ratio} |")
        prev = c if c else prev

    # --- 実効コスト C(B) が max_tokens でどう変わるか ---
    print("\n## 実効コスト C(B)（JSONL、小さいほど良い）\n")
    print(f"| max_tokens | {header} | 最良B |")
    print("|---" * (len(LEVELS) + 2) + "|")
    for mt in MTS:
        row, best, bestc = [], None, float("inf")
        for b in LEVELS:
            keys = [k for k in results if k[0] == mt and k[2] == b and k[1] == "jsonl"]
            req = sum(sent[k] for k in keys)
            cor = sum(results[k].get("correct") or 0 for k in keys)
            if not req:
                row.append("-")
                continue
            c = req / cor if cor else float("inf")
            row.append(f"{c:.4f}" if cor else "∞")
            if c < bestc:
                best, bestc = b, c
        print(f"| {mt} | {' | '.join(row)} | **B={best}** |")

    # --- 形式差が max_tokens によらず再現するか ---
    print("\n## 切断時の形式別回収率（p3-100 の再現性確認）\n")
    print("| 形式 | 切断req | 送信アイテム | 回収 | 回収率 |")
    print("|---|---|---|---|---|")
    for fmt in FORMATS:
        rs = [results[k] for k in results
              if k[1] == fmt and results[k].get("finish_reason") == "length"]
        s = sum(r["items_sent"] for r in rs)
        m = sum(r.get("id_matched") or 0 for r in rs)
        print(f"| {fmt} | {len(rs)} | {s} | {m} | **{m / s:.1%}** |" if s
              else f"| {fmt} | 0 | - | - | 切断なし |")

    # --- reasoning 枯渇（content が空）の発生状況 ---
    print("\n## reasoning 枯渇（content が空の全損）\n")
    print("| max_tokens | 発生数 / 全req | 発生したB |")
    print("|---|---|---|")
    for mt in MTS:
        rs = [results[k] for k in results if k[0] == mt]
        empty = [r for r in rs if r.get("content_empty")]
        bs = sorted({r["batch_size"] for r in empty})
        print(f"| {mt} | {len(empty)} / {len(rs)} | {bs if bs else '-'} |")


if __name__ == "__main__":
    main()
