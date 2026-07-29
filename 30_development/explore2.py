"""探索run p3-000b: 出力を重くしたときの限界を探す。

p3-000 で「ラベルのみ出力」なら B=96 でも完璧と判明した。実務のバッチ処理は
複数フィールドの構造化出力が多く、1アイテムあたりの出力が重いほど壊れやすいはず。
そこで出力の重さを変えた3タスクで、劣化が始まる B を探す。

使い方: uv run python 30_development/explore2.py
"""

import json
import random
import sys
from dataclasses import dataclass

import config
from client import Attempt, FatalAPIError, RunAborted, SakuraClient
from dataset import load_all
from runner import Runner, chunk, now_iso

RUN_ID = "p3-000b"
CAP = 40
MAX_TOKENS = 8192

# 出力の重さが異なる3水準。共通部分は同じで、要求するフィールドだけ変える
TASKS = {
    "label": (
        "各レビューを positive / negative に分類してください。\n"
        '出力: {"id": "<入力のid>", "label": "positive|negative"}'
    ),
    "label_reason": (
        "各レビューを positive / negative に分類し、判断根拠を20〜40字で述べてください。\n"
        '出力: {"id": "<入力のid>", "label": "positive|negative", "reason": "<20〜40字>"}'
    ),
    "structured": (
        "各レビューについて、分類・根拠・確信度・言及されている観点を出力してください。\n"
        '出力: {"id": "<入力のid>", "label": "positive|negative", '
        '"reason": "<20〜40字>", "confidence": <0.0〜1.0の数値>, '
        '"aspects": ["<観点1>", "<観点2>"]}'
    ),
}

SYSTEM_HEAD = (
    "あなたは日本語レビューの分析器です。\n"
    "ルール:\n"
    "- 入力に含まれる id をそのまま返すこと\n"
    "- すべてのアイテムについて必ず1件ずつ出力すること\n"
    "- 説明文・前置き・コードフェンスを出力しないこと\n"
)


def build(batch, task_key: str) -> list[dict]:
    user = "\n".join(
        json.dumps({"id": it.id, "text": it.text}, ensure_ascii=False) for it in batch
    )
    system = (
        SYSTEM_HEAD + TASKS[task_key]
        + "\n出力形式: 1行につき1アイテムの JSON オブジェクトだけを出力する。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def main() -> None:
    levels = [8, 32, 96]
    tasks = ["label_reason", "structured"]
    planned = sum(96 // b for b in levels) * len(tasks)
    print(f"⚠️  {RUN_ID}: 計画 {planned} req（上限 {CAP}）。7月枠で実行します。")

    api_key = config.load_api_key()
    client = SakuraClient(api_key=api_key)
    items, truth, _ = load_all()
    plan = {"levels": {}, "planned": planned, "cap": CAP}
    runner = Runner(
        run_id=RUN_ID, plan=plan, items=items, truth=truth,
        client=client, model=config.MODEL, max_tokens=MAX_TOKENS, log_raw_content=True,
    )
    runner._write_meta()

    rows = []
    shuffled = random.Random(config.BASE_SEED + 1).sample(items, len(items))
    try:
        for task_key in tasks:
            for b in levels:
                for idx, batch in enumerate(chunk(shuffled, b)):
                    sent_ids = [it.id for it in batch]
                    payload = {
                        "model": config.MODEL,
                        "messages": build(batch, task_key),
                        "temperature": config.TEMPERATURE,
                        "max_tokens": MAX_TOKENS,
                        "stream": False,
                    }
                    captured = {}

                    def on_send(n: int) -> None:
                        runner.attempts_sent += 1
                        runner._log({"run_id": RUN_ID, "timestamp": now_iso(),
                                     "event": "sent", "task": task_key,
                                     "batch_size": b, "batch_index": idx, "attempt": n})

                    def on_attempt(att: Attempt) -> None:
                        rec = {"run_id": RUN_ID, "timestamp": now_iso(), "event": "result",
                               "task": task_key, "batch_size": b, "batch_index": idx,
                               "attempt": att.attempt, "http_status": att.http_status,
                               "latency_ms": att.latency_ms, "error": att.error}
                        if att.http_status == 200 and att.response:
                            ch = (att.response.get("choices") or [{}])[0]
                            msg = ch.get("message") or {}
                            content = msg.get("content") or ""
                            usage = att.response.get("usage") or {}
                            # 行ごとに復元し、欠落・破損を数える
                            recs, broken = [], 0
                            for line in content.splitlines():
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    o = json.loads(line)
                                    recs.append(o) if isinstance(o, dict) else broken
                                except json.JSONDecodeError:
                                    broken += 1
                            got = {str(o.get("id")) for o in recs} & set(sent_ids)
                            correct = sum(
                                1 for o in recs
                                if str(o.get("id")) in truth
                                and str(o.get("label", "")).strip().lower() == truth[str(o.get("id"))]
                            )
                            rec.update({
                                "output_tokens": usage.get("completion_tokens"),
                                "finish_reason": ch.get("finish_reason"),
                                "items_sent": len(batch), "items_returned": len(recs),
                                "id_matched": len(got), "broken_lines": broken,
                                "correct": correct, "raw_tail": content[-200:],
                            })
                            captured.update(rec)
                        runner._log(rec)

                    try:
                        client.chat(payload, on_attempt, on_send)
                    except (RunAborted, FatalAPIError):
                        raise
                    if captured:
                        rows.append(captured)
    except (RunAborted, FatalAPIError) as e:
        print(f"停止: {e}", file=sys.stderr)
    finally:
        client.close()

    print(f"\n=== 結果（送信 {runner.attempts_sent} req）===")
    print(f"{'task':14s} {'B':>4} {'返却':>8s} {'ID一致':>8s} {'正解':>8s} {'破損行':>6s} {'out_tok':>8s} {'finish':10s}")
    for r in rows:
        print(f"{r['task']:14s} {r['batch_size']:4d} "
              f"{r['items_returned']:3d}/{r['items_sent']:3d} {r['id_matched']:4d}/{r['items_sent']:3d} "
              f"{r['correct']:4d}/{r['items_sent']:3d} {r['broken_lines']:6d} "
              f"{r['output_tokens']:8d} {str(r['finish_reason']):10s}")

    print("\n=== タスク×水準サマリ（アイテム回収率・正解率）===")
    for task_key in tasks:
        for b in levels:
            sub = [r for r in rows if r["task"] == task_key and r["batch_size"] == b]
            if not sub:
                continue
            sent = sum(r["items_sent"] for r in sub)
            matched = sum(r["id_matched"] for r in sub)
            cor = sum(r["correct"] for r in sub)
            trunc = sum(1 for r in sub if r["finish_reason"] == "length")
            print(f"{task_key:14s} B={b:3d}  回収 {matched:3d}/{sent:3d} ({matched/sent:.1%})  "
                  f"正解 {cor:3d}/{sent:3d} ({cor/sent:.1%})  切断 {trunc}/{len(sub)}req  "
                  f"C(B)={len(sub)/cor if cor else float('inf'):.4f}")


if __name__ == "__main__":
    main()
