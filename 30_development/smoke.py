"""試走 p3-001。実験設計 §2.2 のチェックリストを実施する。

消費上限 30 req をコードで強制。実行前に taiki さんの承認が必要（CLAUDE.md 安全弁）。

確認項目:
1. モデル ID の利用可否（/v1/models があれば一覧も取得）
2. reasoning の挙動（message のキー一覧・生出力を保存 / reasoning_effort の受理確認）
3. max_tokens の校正材料（B=32 の実測最大出力トークン）
4. finish_reason の観察
5. temperature=0.0 の受理
6. 429 時の Retry-After（発生した場合のみ）
7. データ難度（B=1 × 易6+難6 の正解率）
8. 消費カウント突合用に全送信を write-ahead でログ

使い方: uv run python smoke.py [--model gpt-oss-120b]
"""

import argparse
import json
import random
import sys

import httpx

import config
from client import FatalAPIError, RunAborted, SakuraClient
from dataset import load_all
from runner import Runner, now_iso

RUN_ID = "p3-001"
CAP = 30
SMOKE_MAX_TOKENS = 16384  # 校正前なので十分大きく取り、切断を避けて実測する


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.MODEL)
    args = ap.parse_args()

    print(f"⚠️  {RUN_ID}: 最大 {CAP} req を消費します。承認済みであることが前提です。")
    api_key = config.load_api_key()
    client = SakuraClient(api_key=api_key)
    items, truth, difficulty = load_all()

    # ランナーの予算ガード・write-aheadログ機構を流用（plan は smoke 専用）
    plan = {"levels": {}, "planned": CAP, "cap": CAP}
    runner = Runner(
        run_id=RUN_ID, plan=plan, items=items, truth=truth,
        client=client, model=args.model, max_tokens=SMOKE_MAX_TOKENS,
        log_raw_content=True,   # reasoning 混入等を事後検証できるよう生出力を保存
    )
    runner._write_meta()
    report: dict = {"model": args.model, "checked_at": now_iso()}

    rng = random.Random(config.BASE_SEED)
    easy_pool = [it for it in items if difficulty[it.id] == "easy"]
    hard_pool = [it for it in items if difficulty[it.id] == "hard"]
    easy6 = rng.sample(easy_pool, 6)
    hard6 = rng.sample(hard_pool, 6)
    assert len(easy6) == 6 and len(hard6) == 6

    try:
        # 0) /v1/models の有無（GET。チャット枠外の想定だが結果は記録する）
        try:
            r = httpx.get(
                f"{config.API_BASE}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            report["models_endpoint"] = {
                "status": r.status_code,
                "models": [m.get("id") for m in r.json().get("data", [])][:40]
                if r.status_code == 200 else None,
            }
        except (httpx.HTTPError, ValueError) as e:
            report["models_endpoint"] = {"error": type(e).__name__}

        # 1) 疎通 + reasoning/finish_reason/usage の観察（最小バッチ B=2）— 1 req
        runner._process_unit(trial=0, b=2, fmt="json_array", idx=0, batch=rng.sample(items, 2))

        # 2) reasoning_effort の受理確認 — 1 req（400 なら未対応と記録して続行）
        probe_batch = rng.sample(items, 2)
        from prompts import build_messages
        probe_payload = {
            "model": args.model,
            "messages": build_messages(probe_batch, "json_array"),
            "temperature": config.TEMPERATURE,
            "max_tokens": 2048,
            "stream": False,
            "reasoning_effort": "low",
        }

        def probe_on_send(n: int) -> None:
            runner.attempts_sent += 1
            runner._log({"run_id": RUN_ID, "timestamp": now_iso(), "event": "sent",
                         "task": "probe_reasoning_effort", "attempt": n})

        def probe_on_attempt(att) -> None:
            runner._log({"run_id": RUN_ID, "timestamp": now_iso(), "event": "result",
                         "task": "probe_reasoning_effort", "attempt": att.attempt,
                         "http_status": att.http_status, "latency_ms": att.latency_ms,
                         "error": att.error, "error_body": att.error_body})

        try:
            client.chat(probe_payload, probe_on_attempt, probe_on_send)
            report["reasoning_effort_accepted"] = True
        except FatalAPIError as e:
            report["reasoning_effort_accepted"] = False
            report["reasoning_effort_error"] = str(e)[:200]

        # 3) B=1 × 12 件（易6・難6）で難度確認（json_array）— 12 req
        for i, it in enumerate(easy6 + hard6):
            runner._process_unit(trial=0, b=1, fmt="json_array", idx=i, batch=[it])

        # 4) B=4 両形式、B=32 両形式（max_tokens 校正の実測材料）— 4 req
        b4 = rng.sample(items, 4)
        for fmt in ("json_array", "jsonl"):
            runner._process_unit(trial=0, b=4, fmt=fmt, idx=0, batch=b4)
        b32 = random.Random(config.BASE_SEED + 1).sample(items, 32)
        for fmt in ("json_array", "jsonl"):
            runner._process_unit(trial=0, b=32, fmt=fmt, idx=0, batch=b32)

    except (RunAborted, FatalAPIError) as e:
        print(f"停止: {e}", file=sys.stderr)
    finally:
        client.close()

    # ログから要約を作る（詳細は目視: raw_content を確認すること）
    recs = [json.loads(l) for l in runner.log_path.open() if l.strip()]
    results = [r for r in recs if r.get("event") == "result"]
    ok = [r for r in results if r.get("http_status") == 200]
    b1 = [r for r in ok if r.get("batch_size") == 1]
    max_out = max((r.get("output_tokens") or 0 for r in ok), default=0)
    report.update(
        {
            "requests_sent": runner.attempts_sent,
            "http_ok": len(ok),
            "finish_reasons": sorted({str(r.get("finish_reason")) for r in ok}),
            "message_keys_seen": sorted(
                {k for r in ok for k in (r.get("message_keys") or [])}
            ),
            "max_output_tokens_observed": max_out or None,
            "suggested_max_tokens(×3)": max_out * 3 or None,
            "b1_accuracy_12items": (
                sum(r.get("correct") or 0 for r in b1) / len(b1) if b1 else None
            ),
            "retry_after_seen": sorted(
                {r["retry_after"] for r in results if r.get("retry_after")}
            ),
        }
    )
    out = runner.log_path.parent / f"{RUN_ID}.report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nレポート: {out}")
    print("→ この結果で max_tokens を確定し、必要ならデータ難度を調整してから p3-002 へ。")
    print("→ コントロールパネルの消費表示と requests_sent の突合も忘れずに（設計 §2.2-8）。")


if __name__ == "__main__":
    main()
