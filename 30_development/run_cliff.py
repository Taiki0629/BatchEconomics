"""追試 p3-200: max_tokens を変えると崖は動くか。

p3-100 で「崩壊の原因は出力トークン予算の枯渇」と分かった。ならば予算そのものを
変えれば崖の位置が比例して動くはず。これが確かめられれば、次の実務指針が裏付けられる。

    最適バッチサイズ B* ≈ max_tokens ÷ アイテムあたり出力トークン

タスクは `label_reason` に固定（p3-100 で最も明瞭な崖を示したため）。
アイテムあたり約290トークンなので、予測される崖の位置は:
    max_tokens=2048 → B≈7 / 4096 → B≈14 / 8192 → B≈28 / 16384 → B≈56

要因: max_tokens 4水準 × B 5水準 × 形式 2種 × 2試行 = 384 req

使い方: uv run python 30_development/run_cliff.py
"""

import json
import random
import sys

import config
from client import Attempt, FatalAPIError, RunAborted, SakuraClient
from dataset import load_all
from runner import Runner, chunk, now_iso
from run_main import build_messages, parse

RUN_ID = "p3-200"
TASK = "label_reason"
MAX_TOKENS_LEVELS = [2048, 4096, 8192, 16384]
LEVELS = [8, 16, 32, 48, 96]
FORMATS = ("json_array", "jsonl")
TRIALS = 2
CAP = 450


def main() -> None:
    planned = sum(96 // b for b in LEVELS) * len(MAX_TOKENS_LEVELS) * len(FORMATS) * TRIALS
    print(f"⚠️  {RUN_ID}: 計画 {planned} req（上限 {CAP}）")

    api_key = config.load_api_key()
    client = SakuraClient(api_key=api_key)
    items, truth, difficulty = load_all()
    plan = {"levels": {b: TRIALS for b in LEVELS}, "planned": planned, "cap": CAP}
    # max_tokens を振るため、meta には代表値ではなく水準リストを記録する
    runner = Runner(
        run_id=RUN_ID, plan=plan, items=items, truth=truth,
        client=client, model=config.MODEL, max_tokens=-1,   # -1 = 可変（下で個別指定）
    )
    runner._write_meta()
    done = runner.done

    try:
        for trial in range(1, TRIALS + 1):
            shuffled = random.Random(config.BASE_SEED + trial).sample(items, len(items))
            for mt in MAX_TOKENS_LEVELS:
                for b in LEVELS:
                    for fmt in FORMATS:
                        for idx, batch in enumerate(chunk(shuffled, b)):
                            key = f"{mt}:{fmt}:{b}:{trial}:{idx}"
                            if key in done:
                                continue
                            sent_ids = [it.id for it in batch]
                            payload = {
                                "model": config.MODEL,
                                "messages": build_messages(batch, TASK, fmt),
                                "temperature": config.TEMPERATURE,
                                "max_tokens": mt,
                                "stream": False,
                            }
                            base = {"run_id": RUN_ID, "task": TASK, "format": fmt,
                                    "batch_size": b, "trial": trial, "batch_index": idx,
                                    "items_sent": len(batch), "max_tokens": mt}

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
                                        seen = {}
                                        for o in recs:
                                            rid = str(o.get("id", "")).strip()
                                            if rid in sent_ids and rid not in seen:
                                                seen[rid] = str(o.get("label", "")).strip().lower()
                                        rec.update({
                                            "output_tokens": usage.get("completion_tokens"),
                                            "finish_reason": ch.get("finish_reason"),
                                            "content_empty": len(content) == 0,
                                            "parse_ok": ok, "broken_lines": broken,
                                            "items_returned": len(recs), "id_matched": len(seen),
                                            "correct": sum(1 for it in batch
                                                           if seen.get(it.id) == truth[it.id]),
                                            "per_item": [
                                                {"pos": i, "item_id": it.id,
                                                 "predicted": seen.get(it.id),
                                                 "correct": seen.get(it.id) == truth[it.id]}
                                                for i, it in enumerate(batch)],
                                        })
                                except Exception as e:
                                    rec["error"] = f"analysis_{type(e).__name__}"
                                finally:
                                    runner._log(rec)

                            try:
                                client.chat(payload, on_attempt, on_send)
                            except (RunAborted, FatalAPIError):
                                raise
                            except Exception:
                                pass
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
