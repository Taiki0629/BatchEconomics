"""無償枠の消費集計（/budget コマンドの実体）。

40_test/logs/*.jsonl を走査し、当月のリクエスト数を API 種別ごとに集計して残枠を表示する。
リトライも HTTP ステータスに関わらず、送信した attempt はすべて 1 req として数える。
このスクリプトは表示のみで API を呼ばない。
"""

import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import config

LIMITS = {
    "chat": 3000,
    "embeddings": 10000,
    "asr": 50,
    "tts": 50,
}


def main() -> None:
    month = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m")
    counts: Counter[str] = Counter()
    by_run: Counter[str] = Counter()
    total_lines = 0
    for path in sorted(config.LOGS_DIR.glob("*.jsonl")):
        for line in path.open():
            if not line.strip():
                continue
            total_lines += 1
            r = json.loads(line)
            # write-ahead の "sent" 行のみを数える（result 行と二重計上しない。
            # 送信直前に必ず書かれるため、応答前にクラッシュした送信も漏れない）
            if r.get("event") != "sent":
                continue
            if str(r.get("timestamp", "")).startswith(month):
                # 本プロジェクトの実験は chat のみ（設計スコープ）
                counts["chat"] += 1
                by_run[r.get("run_id", path.stem)] += 1

    print(f"# 無償枠消費状況（{month}）\n")
    print("| API | 消費 | 上限 | 残り | 消費率 |")
    print("|---|---|---|---|---|")
    for api, limit in LIMITS.items():
        used = counts.get(api, 0)
        rate = used / limit
        warn = " ⚠️" if rate > 0.7 else ""
        print(f"| {api} | {used} | {limit} | {limit - used} | {rate:.1%}{warn} |")
    if by_run:
        print("\n## run別内訳（当月）")
        for run_id, n in sorted(by_run.items()):
            print(f"- {run_id}: {n} req")
    if total_lines == 0:
        print("\n(ログなし: 消費 0)")


if __name__ == "__main__":
    main()
