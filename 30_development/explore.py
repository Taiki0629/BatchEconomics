"""探索run p3-000: 劣化が始まる境界を探す。

試走 p3-001 で B=32 まで正解率100%・パース失敗ゼロだったため、本実験（884req）を
投じる前に「どこで壊れるか」を確認する。7月の無償枠は 8/1 にリセットされ未使用分は
失われるため、この探索は実質ゼロコストで行える。

確認すること:
- B=48, 96 でも構造が保たれるか（H1 の右打ち切りが起きるか）
- 出力トークン数が max_tokens に迫らないか

使い方: uv run python 30_development/explore.py
"""

import json
import random
import sys

import config
from client import FatalAPIError, RunAborted, SakuraClient
from dataset import load_all
from runner import Runner, chunk

RUN_ID = "p3-000"
CAP = 30
LEVELS = [48, 96]
TRIALS = 3
MAX_TOKENS = 8192


def main() -> None:
    planned = sum(96 // b for b in LEVELS) * len(("json_array", "jsonl")) * TRIALS
    print(f"⚠️  {RUN_ID}: 計画 {planned} req（上限 {CAP}）。7月枠で実行します。")
    api_key = config.load_api_key()
    client = SakuraClient(api_key=api_key)
    items, truth, _ = load_all()

    plan = {"levels": {b: TRIALS for b in LEVELS}, "planned": planned, "cap": CAP}
    runner = Runner(
        run_id=RUN_ID, plan=plan, items=items, truth=truth,
        client=client, model=config.MODEL, max_tokens=MAX_TOKENS,
        log_raw_content=True,
    )
    runner._write_meta()

    try:
        for trial in range(1, TRIALS + 1):
            shuffled = random.Random(config.BASE_SEED + trial).sample(items, len(items))
            for b in LEVELS:
                for fmt in ("json_array", "jsonl"):
                    for idx, batch in enumerate(chunk(shuffled, b)):
                        runner._process_unit(trial, b, fmt, idx, batch)
    except (RunAborted, FatalAPIError) as e:
        print(f"停止: {e}", file=sys.stderr)
    finally:
        client.close()

    recs = [json.loads(l) for l in runner.log_path.open() if l.strip()]
    res = [r for r in recs if r.get("event") == "result" and r.get("http_status") == 200]
    print(f"\n=== 結果（送信 {runner.attempts_sent} req）===")
    print(f"{'B':>4} {'format':10s} {'parse':6s} {'返却':>7s} {'正解':>7s} {'out_tok':>8s} {'finish':8s}")
    for r in sorted(res, key=lambda x: (x["batch_size"], x["format"], x["trial"])):
        print(f"{r['batch_size']:4d} {r['format']:10s} {str(r['parse_ok']):6s} "
              f"{r['items_returned']:3d}/{r['items_sent']:3d} {r['correct']:3d}/{r['items_sent']:3d} "
              f"{r['output_tokens']:8d} {str(r['finish_reason']):8s}")

    print("\n=== 水準別サマリ ===")
    for b in LEVELS:
        for fmt in ("json_array", "jsonl"):
            sub = [r for r in res if r["batch_size"] == b and r["format"] == fmt]
            if not sub:
                continue
            sent = sum(r["items_sent"] for r in sub)
            got = sum(r["items_returned"] for r in sub)
            cor = sum(r["correct"] for r in sub)
            pok = sum(1 for r in sub if r["parse_ok"])
            reqs = len(sub)
            print(f"B={b:3d} {fmt:10s} parse_ok {pok}/{reqs}  回収 {got}/{sent}  "
                  f"正解 {cor}/{sent} ({cor/sent:.1%})  C(B)={reqs/cor if cor else float('inf'):.4f} req/正解")


if __name__ == "__main__":
    main()
