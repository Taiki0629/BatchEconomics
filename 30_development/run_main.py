"""本実験 p3-100: 出力トークン上限が作る「崖」の測定。

探索（p3-000 / p3-000b）で判明した事実に基づく再設計:
- 出力が軽い（ラベルのみ）なら B=96 でも完璧。C(B) は単調減少で崖が来ない
- 出力を重くすると B=96 で finish_reason=length の切断が起き、崩壊する
- 崩壊には2つのモードがある
    A) reasoning が出力予算を食い尽くし content が空になる → 全損
    B) 出力途中で切断される → JSONL は部分回収できるが JSON 配列は全損（H6）

したがって測るべきは「U字カーブ」ではなく「崖の位置と落ち方」。

要因:
- タスク（出力の重さ）: label / label_reason / structured
- バッチサイズ B: 8, 16, 32, 48, 96
- 出力形式: json_array / jsonl  ← 崖での挙動差が H6 の核心

使い方: uv run python 30_development/run_main.py [--trials 2]
"""

import argparse
import json
import random
import sys

import config
from client import Attempt, FatalAPIError, RunAborted, SakuraClient
from dataset import load_all
from runner import Runner, chunk, now_iso

RUN_ID = "p3-100"
MAX_TOKENS = 8192
LEVELS = [8, 16, 32, 48, 96]
FORMATS = ("json_array", "jsonl")

TASK_INSTRUCTIONS = {
    "label": '{"id": "<入力のid>", "label": "positive|negative"}',
    "label_reason": '{"id": "<入力のid>", "label": "positive|negative", "reason": "<20〜40字>"}',
    "structured": ('{"id": "<入力のid>", "label": "positive|negative", "reason": "<20〜40字>", '
                   '"confidence": <0.0〜1.0>, "aspects": ["<観点1>", "<観点2>"]}'),
}
TASK_VERB = {
    "label": "各レビューを positive / negative に分類してください。",
    "label_reason": "各レビューを positive / negative に分類し、判断根拠を20〜40字で述べてください。",
    "structured": "各レビューについて、分類・根拠・確信度・言及されている観点を出力してください。",
}
FORMAT_INSTRUCTION = {
    "json_array": "出力形式: 入力と同数の要素を持つ JSON 配列だけを出力する。各要素は次の形式:",
    "jsonl": "出力形式: 1行につき1アイテムの JSON オブジェクトだけを出力する。各行は次の形式:",
}


def build_messages(batch, task: str, fmt: str) -> list[dict]:
    system = (
        "あなたは日本語レビューの分析器です。\n"
        f"{TASK_VERB[task]}\n"
        "ルール:\n"
        "- 入力に含まれる id をそのまま返すこと\n"
        "- すべてのアイテムについて必ず1件ずつ出力すること\n"
        "- 説明文・前置き・コードフェンスを出力しないこと\n"
        f"{FORMAT_INSTRUCTION[fmt]}\n{TASK_INSTRUCTIONS[task]}"
    )
    if fmt == "json_array":
        user = json.dumps([{"id": it.id, "text": it.text} for it in batch], ensure_ascii=False)
    else:
        user = "\n".join(
            json.dumps({"id": it.id, "text": it.text}, ensure_ascii=False) for it in batch
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse(content: str, fmt: str) -> tuple[list[dict], int, bool]:
    """(復元レコード, 破損数, パース成功) を返す。"""
    body = content.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0]
    if fmt == "json_array":
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return [], 0, False          # 配列は1文字でも壊れると全損
        if not isinstance(data, list):
            return [], 0, False
        recs = [r for r in data if isinstance(r, dict)]
        return recs, len(data) - len(recs), True
    recs, broken = [], 0
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            broken += 1                  # JSONL は壊れた行だけ捨てて他は回収できる
            continue
        recs.append(o) if isinstance(o, dict) else broken
    return recs, broken, len(recs) > 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--cap", type=int, default=700)
    args = ap.parse_args()

    per_cell = sum(96 // b for b in LEVELS)          # 24 req
    planned = per_cell * len(TASK_VERB) * len(FORMATS) * args.trials
    print(f"⚠️  {RUN_ID}: 計画 {planned} req（上限 {args.cap}）")

    api_key = config.load_api_key()
    client = SakuraClient(api_key=api_key)
    items, truth, difficulty = load_all()
    plan = {"levels": {b: args.trials for b in LEVELS}, "planned": planned, "cap": args.cap}
    runner = Runner(
        run_id=RUN_ID, plan=plan, items=items, truth=truth,
        client=client, model=config.MODEL, max_tokens=MAX_TOKENS,
    )
    runner._write_meta()
    done = runner.done

    try:
        for trial in range(1, args.trials + 1):
            shuffled = random.Random(config.BASE_SEED + trial).sample(items, len(items))
            for task in TASK_VERB:
                for b in LEVELS:
                    for fmt in FORMATS:
                        for idx, batch in enumerate(chunk(shuffled, b)):
                            key = f"{task}:{fmt}:{b}:{trial}:{idx}"
                            if key in done:
                                continue
                            sent_ids = [it.id for it in batch]
                            payload = {
                                "model": config.MODEL,
                                "messages": build_messages(batch, task, fmt),
                                "temperature": config.TEMPERATURE,
                                "max_tokens": MAX_TOKENS,
                                "stream": False,
                            }
                            base = {"run_id": RUN_ID, "task": task, "format": fmt,
                                    "batch_size": b, "trial": trial, "batch_index": idx,
                                    "items_sent": len(batch), "max_tokens": MAX_TOKENS}

                            def on_send(n: int) -> None:
                                runner.attempts_sent += 1
                                runner._log({**base, "timestamp": now_iso(),
                                             "event": "sent", "attempt": n})

                            def on_attempt(att: Attempt) -> None:
                                rec = {**base, "timestamp": now_iso(), "event": "result",
                                       "attempt": att.attempt, "http_status": att.http_status,
                                       "latency_ms": att.latency_ms, "error": att.error}
                                try:
                                    if att.http_status == 200 and att.response:
                                        ch = (att.response.get("choices") or [{}])[0]
                                        ch = ch if isinstance(ch, dict) else {}
                                        msg = ch.get("message") or {}
                                        content = msg.get("content") or ""
                                        usage = att.response.get("usage") or {}
                                        recs, broken, ok = parse(content, fmt)
                                        seen, dup, unknown = {}, 0, 0
                                        for o in recs:
                                            rid = str(o.get("id", "")).strip()
                                            if rid not in sent_ids:
                                                unknown += 1
                                            elif rid in seen:
                                                dup += 1
                                            else:
                                                seen[rid] = str(o.get("label", "")).strip().lower()
                                        per_item = [
                                            {"pos": i, "item_id": it.id,
                                             "difficulty": difficulty[it.id],
                                             "predicted": seen.get(it.id),
                                             "correct": seen.get(it.id) == truth[it.id]}
                                            for i, it in enumerate(batch)
                                        ]
                                        rec.update({
                                            "output_tokens": usage.get("completion_tokens"),
                                            "input_tokens": usage.get("prompt_tokens"),
                                            "finish_reason": ch.get("finish_reason"),
                                            "content_empty": len(content) == 0,
                                            "parse_ok": ok, "broken_lines": broken,
                                            "items_returned": len(recs),
                                            "id_matched": len(seen),
                                            "dup_id_count": dup, "unknown_id_count": unknown,
                                            "correct": sum(1 for p in per_item if p["correct"]),
                                            "per_item": per_item,
                                        })
                                except Exception as e:
                                    rec["error"] = f"analysis_{type(e).__name__}"
                                finally:
                                    runner._log(rec)

                            try:
                                client.chat(payload, on_attempt, on_send)
                            except Exception:
                                pass          # 失敗も記録済み。次のセルへ進む
                            runner._mark_done(key)
                            done.add(key)
    except (RunAborted, FatalAPIError) as e:
        print(f"停止: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("中断。チェックポイントから再開できます。", file=sys.stderr)
    finally:
        client.close()
    print(f"完了: 送信 {runner.attempts_sent} req → {runner.log_path}")


if __name__ == "__main__":
    main()
