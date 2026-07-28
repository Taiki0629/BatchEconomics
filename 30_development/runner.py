"""実験ランナー。

実験設計 `20_design/02_experiment_design.md` §1〜2 を実行する:
- 水準×形式×trial のバッチを順に送信し、attempt 単位で JSONL ログを書く
- **write-ahead 記録**: 送信直前に event="sent" 行、応答後に event="result" 行を書く。
  Ctrl-C・クラッシュ・電源断でも消費の痕跡が必ず残る（消費集計は "sent" 行を数える）
- シャッフルシードは trial ごと・形式間で共有（対応付き比較）
- 水準の実行順は trial ごとにローテーション（順序効果対策）
- チェックポイントで中断・再開可能（処理済み単位はスキップ、消費の二重計上なし）
- 予算ガード: cap を超える送信を拒否（リトライ含む全 attempt をカウント）
- 再開時に model / max_tokens が meta.json と食い違えば起動を拒否（条件混在の防止）

使い方:
  uv run python runner.py --run-id p3-002 --max-tokens 4096 --dry-run   # 計画確認のみ
  uv run python runner.py --run-id p3-002 --max-tokens 4096
"""

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

import config
from client import (
    Attempt,
    FatalAPIError,
    RunAborted,
    SakuraClient,
    TransportExhausted,
)
from dataset import Item, load_all
from prompts import FORMATS, build_messages, parse_output, score_batch

JST = ZoneInfo("Asia/Tokyo")

# 設計 §2.1: 計画リクエスト数 + リトライ予備 を cap とする（P3 合計 30+766+118=914 を厳守）
RUN_PLANS: dict[str, dict] = {
    "p3-002": {"levels": {1: 1, 2: 3, 4: 3, 8: 3}, "planned": 696, "cap": 766},
    "p3-003": {"levels": {16: 6, 32: 6}, "planned": 108, "cap": 118},
}


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def chunk(seq: list, size: int) -> list[list]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def plan_units(plan: dict, items: list[Item]) -> list[tuple[int, int, str, int, list[Item]]]:
    """(trial, B, fmt, batch_idx, batch) を実行順で列挙する。

    - trial ごとに水準の実行順をローテーション（順序効果対策）
    - シャッフルシードは形式間で共有（BASE_SEED + trial）
    """
    units = []
    levels = sorted(plan["levels"])
    max_trials = max(plan["levels"].values())
    for trial in range(1, max_trials + 1):
        active = [b for b in levels if plan["levels"][b] >= trial]
        rot = (trial - 1) % len(active)
        ordered = active[rot:] + active[:rot]
        shuffled = random.Random(config.BASE_SEED + trial).sample(items, len(items))
        for b in ordered:
            batches = chunk(shuffled, b)
            for fmt in FORMATS:
                for idx, batch in enumerate(batches):
                    units.append((trial, b, fmt, idx, batch))
    return units


def unit_key(trial: int, b: int, fmt: str, idx: int) -> str:
    return f"{fmt}:{b}:{trial}:{idx}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Runner:
    def __init__(
        self,
        run_id: str,
        plan: dict,
        items: list[Item],
        truth: dict[str, str],
        client,
        model: str,
        max_tokens: int,
        logs_dir: Path = config.LOGS_DIR,
        log_raw_content: bool = False,
    ):
        self.run_id = run_id
        self.plan = plan
        self.items = items
        self.truth = truth
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.log_raw_content = log_raw_content
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = logs_dir / f"{run_id}.jsonl"
        self.ckpt_path = logs_dir / f"{run_id}.checkpoint.txt"
        self.meta_path = logs_dir / f"{run_id}.meta.json"
        self.done: set[str] = set()
        if self.ckpt_path.exists():
            self.done = set(self.ckpt_path.read_text().split())
        # 予算ガードの基準は「送信済み attempt 数」= write-ahead("sent")行数（再開時も引き継ぐ）
        self.attempts_sent = 0
        if self.log_path.exists():
            for line in self.log_path.open():
                if line.strip() and json.loads(line).get("event") == "sent":
                    self.attempts_sent += 1
        if hasattr(client, "before_send"):
            client.before_send = self._budget_guard

    def _budget_guard(self) -> None:
        if self.attempts_sent >= self.plan["cap"]:
            raise RunAborted(
                f"予算上限 {self.plan['cap']} req に到達（送信済み {self.attempts_sent}）。"
                " 継続するには消費実績を確認し、再見積もり→承認が必要です。"
            )

    def _write_meta(self) -> None:
        if self.meta_path.exists():
            # 再開時: 実験条件の混在を防ぐ（条件を変えるなら新しい run_id を切る）
            old = json.loads(self.meta_path.read_text())
            if old.get("model") != self.model or old.get("max_tokens") != self.max_tokens:
                raise RunAborted(
                    f"再開パラメータが meta.json と不一致: "
                    f"model {old.get('model')}→{self.model}, "
                    f"max_tokens {old.get('max_tokens')}→{self.max_tokens}。"
                    " 条件を変える場合は新しい run_id を使ってください。"
                )
            return
        meta = {
            "run_id": self.run_id,
            "started_at": now_iso(),
            "purpose": "バッチサイズ×プロンプト形式の実測（20_design/02_experiment_design.md）",
            "model": self.model,
            "temperature": config.TEMPERATURE,
            "max_tokens": self.max_tokens,
            "base_seed": config.BASE_SEED,
            "levels": self.plan["levels"],
            "planned_requests": self.plan["planned"],
            "approved_cap": self.plan["cap"],
            "dataset_sha256": _sha256(config.DATASET_ITEMS),
            "labels_sha256": _sha256(config.DATASET_LABELS),
            "prompts_py_sha256": _sha256(Path(__file__).parent / "prompts.py"),
            "dataset_items": len(self.items),
            "python": sys.version.split()[0],
            "httpx": httpx.__version__,
        }
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    def _log(self, record: dict) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

    def _mark_done(self, key: str) -> None:
        with self.ckpt_path.open("a") as f:
            f.write(key + "\n")
            f.flush()

    def _base_record(self, trial: int, b: int, fmt: str, idx: int, batch: list[Item]) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": now_iso(),
            "model": self.model,
            "max_tokens": self.max_tokens,
            "task": "sentiment",
            "format": fmt,
            "batch_size": b,
            "trial": trial,
            "batch_index": idx,
            "items_sent": len(batch),
        }

    def _process_unit(self, trial: int, b: int, fmt: str, idx: int, batch: list[Item]) -> None:
        payload = {
            "model": self.model,
            "messages": build_messages(batch, fmt),
            "temperature": config.TEMPERATURE,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        def on_send(attempt_no: int) -> None:
            # write-ahead: 送信の意図を先に永続化する（応答前に落ちても消費の痕跡が残る）
            self.attempts_sent += 1
            rec = self._base_record(trial, b, fmt, idx, batch)
            rec.update({"event": "sent", "attempt": attempt_no})
            self._log(rec)

        def on_attempt(att: Attempt) -> None:
            rec = self._base_record(trial, b, fmt, idx, batch)
            rec.update(
                {
                    "event": "result",
                    "attempt": att.attempt,
                    "http_status": att.http_status,
                    "latency_ms": att.latency_ms,
                    "error": att.error,
                    "error_body": att.error_body,
                    "retry_after": att.retry_after,
                    "input_tokens": None,
                    "output_tokens": None,
                    "finish_reason": None,
                    "message_keys": None,
                    "has_reasoning_field": None,
                    "parse_ok": None,
                    "fence_stripped": None,
                    "broken_lines": None,
                    "items_returned": None,
                    "id_match_rate": None,
                    "dup_id_count": None,
                    "unknown_id_count": None,
                    "correct": None,
                    "per_item": [],
                }
            )
            try:
                if att.http_status == 200 and att.response is not None:
                    resp = att.response
                    choices = resp.get("choices") or [{}]
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    message = choice.get("message")
                    message = message if isinstance(message, dict) else {}
                    content = message.get("content") or ""
                    usage = resp.get("usage") or {}
                    parsed = parse_output(content, fmt)
                    score = score_batch(batch, parsed.records, self.truth)
                    rec.update(
                        {
                            "input_tokens": usage.get("prompt_tokens"),
                            "output_tokens": usage.get("completion_tokens"),
                            "finish_reason": choice.get("finish_reason"),
                            "message_keys": sorted(message.keys()),
                            "has_reasoning_field": (
                                "reasoning_content" in message or "reasoning" in message
                            ),
                            "parse_ok": parsed.parse_ok,
                            "fence_stripped": parsed.fence_stripped,
                            "broken_lines": parsed.broken_lines,
                            "items_returned": score.items_returned,
                            "id_match_rate": score.id_match_rate,
                            "dup_id_count": score.dup_id_count,
                            "unknown_id_count": score.unknown_id_count,
                            "correct": score.correct,
                            "per_item": score.per_item,
                        }
                    )
                    if self.log_raw_content:
                        # 試走用: reasoning 混入等を事後検証できるよう生データを残す
                        rec["raw_content"] = content[:4000]
                        rec["usage_raw"] = usage
            except Exception as e:
                # 解析に失敗しても消費記録は必ず残す（ログ喪失が最悪の事態）
                rec["error"] = (rec.get("error") or "") + f"|analysis_{type(e).__name__}"
            finally:
                self._log(rec)

        try:
            self.client.chat(payload, on_attempt, on_send)
        except TransportExhausted:
            # リトライ上限。この条件を failed として先へ進む（設計 §2.3。ログは記録済み）
            pass

    def run(self) -> dict:
        self._write_meta()
        units = plan_units(self.plan, self.items)
        skipped = 0
        processed = 0
        for trial, b, fmt, idx, batch in units:
            key = unit_key(trial, b, fmt, idx)
            if key in self.done:
                skipped += 1
                continue
            self._process_unit(trial, b, fmt, idx, batch)
            self._mark_done(key)
            self.done.add(key)
            processed += 1
        return {
            "units_total": len(units),
            "units_processed": processed,
            "units_skipped": skipped,
            "attempts_sent_total": self.attempts_sent,
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True, choices=sorted(RUN_PLANS))
    ap.add_argument("--model", default=config.MODEL)
    # 校正前の仮値のまま本実行することを防ぐため必須（試走 p3-001 で確定した値を渡す）
    ap.add_argument("--max-tokens", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true", help="送信計画の表示のみ（APIを叩かない）")
    args = ap.parse_args()

    plan = RUN_PLANS[args.run_id]
    items, truth, _ = load_all()
    units = plan_units(plan, items)

    if args.dry_run:
        by = {}
        for trial, b, fmt, _, _ in units:
            by[(b, fmt)] = by.get((b, fmt), 0) + 1
        print(f"run_id={args.run_id} 計画リクエスト数(リトライ除く)={len(units)} cap={plan['cap']}")
        for (b, fmt), n in sorted(by.items()):
            print(f"  B={b:3d} {fmt:10s}: {n} req")
        assert len(units) == plan["planned"], "計画数が設計書と不一致！"
        print("設計書 §2.1 と一致。")
        return

    # ここから先は無償枠を消費する。事前承認（CLAUDE.md 安全弁）が前提。
    print(f"⚠️  {args.run_id}: 最大 {plan['cap']} req を消費します。承認済みであることが前提です。")
    api_key = config.load_api_key()
    client = SakuraClient(api_key=api_key)
    runner = Runner(
        run_id=args.run_id,
        plan=plan,
        items=items,
        truth=truth,
        client=client,
        model=args.model,
        max_tokens=args.max_tokens,
    )
    try:
        result = runner.run()
        print(json.dumps(result, ensure_ascii=False))
    except (RunAborted, FatalAPIError) as e:
        print(f"停止: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("中断されました。チェックポイントから再開できます。", file=sys.stderr)
        sys.exit(130)
    finally:
        client.close()


if __name__ == "__main__":
    main()
