---
name: experiment
description: 実験の再現性を担保する規約。ログスキーマ（JSONL）、乱数シード固定、実行条件の記録、チェックポイントによる中断・再開。実験スクリプトの実装・実行時に必ず参照する。
---

# 実験の再現性規約

## ログスキーマ（write-ahead 方式の JSONL）

保存先: `40_test/logs/<run_id>.jsonl`。1 回の送信（リトライも含む）につき **2 行**:

1. **`"event": "sent"` 行**: 送信直前に書く write-ahead 記録（run_id, timestamp, format, batch_size, trial, batch_index, attempt など）。応答前にクラッシュ・Ctrl-C・電源断が起きても消費の痕跡が残る
2. **`"event": "result"` 行**: 応答後に書く結果記録（下記スキーマ）

**消費集計（/budget）は "sent" 行を数える**。分析は "result" 行を使う。

必須フィールド:

```json
{
  "run_id": "p3-001",
  "timestamp": "2026-08-01T09:00:00+09:00",
  "model": "gpt-oss-120b",
  "task": "sentiment",
  "format": "json_array",
  "batch_size": 8,
  "trial": 1,
  "attempt": 1,
  "http_status": 200,
  "latency_ms": 1234,
  "input_tokens": 456,
  "output_tokens": 789,
  "finish_reason": "stop",
  "parse_ok": true,
  "fence_stripped": false,
  "broken_lines": 0,
  "items_sent": 8,
  "items_returned": 8,
  "id_match_rate": 1.0,
  "dup_id_count": 0,
  "unknown_id_count": 0,
  "correct": 7,
  "per_item": [
    {"pos": 0, "item_id": "d-0012", "expected": "positive", "predicted": "positive", "correct": true}
  ]
}
```

- `format` は `json_array` / `jsonl` の 2 値（プロンプト形式）
- `attempt` は 1 始まり。リトライすると同じ (run_id, task, batch_size, trial) で attempt が増える
- **`per_item` には必ず `pos`（バッチ内位置、0 始まり）を含める**。バッチ内位置による精度劣化（後方ほど壊れる仮説）の検証に必須
- **`finish_reason` は必ず記録**。`length`（max_tokens 切断）由来の失敗は生成品質由来と別カテゴリで集計する
- `latency_ms` はリクエスト送信〜レスポンス完了（送信間インターバルの sleep は含まない）。attempt ごとに記録
- パース失敗時は `parse_ok: false`、`per_item` は復元できた分だけ記録し、復元不能なら空配列
- 同一 id の複数返却は最初の 1 件のみ採用し `dup_id_count` に計上。未送信 id は無視し `unknown_id_count` に計上
- `timestamp` は ISO 8601 / JST。`/budget` が当月集計に使う

## run ヘッダー（実行条件の記録)

各 run の開始時に、同じディレクトリの `<run_id>.meta.json` へ実行条件を保存する:

- run_id、開始日時、目的、承認されたリクエスト見積もり
- モデル ID、プロンプトテンプレートのハッシュ（またはファイルパス+git的な版数）、temperature 等の全パラメータ
- 乱数シード、データセットの版数・件数
- 実行環境（Python バージョン、主要ライブラリのバージョン）

## 乱数シード

- シードは固定値（デフォルト 42）とし、meta.json に必ず記録する
- データのサンプリング・シャッフル・バッチへの割り付けはすべてシード付き `random.Random(seed)` 経由。グローバル乱数を使わない
- 同じ (seed, データセット, 水準) なら同じバッチ構成が再現できること

## 集計時の規約

- unit（format × batch_size × trial × batch_index）ごとに**最後の成功 attempt のみ採用**する（チェックポイント書き込み直前のクラッシュで同一 unit の成功行が稀に重複しうるため）
- 消費リクエスト数は "sent" 行の数（HTTPステータス・成否に関わらず全送信）

## チェックポイント（中断・再開）

- 処理済みの単位（format:batch_size:trial:batch_index）をチェックポイントファイル `40_test/logs/<run_id>.checkpoint.txt`（1行1キーの追記型）に逐次追記する
- スクリプト再実行時はチェックポイントを読み、処理済みをスキップして続きから再開する
- 1 リクエスト完了ごとにログとチェックポイントを flush する（クラッシュしても消費済みリクエストの記録が残ること。無償枠の消費記録が失われるのが最悪の事態）
- Ctrl-C（SIGINT）で安全に停止できること（書きかけ行を作らない）

## 実行の作法

- 実行前に消費見積もりを提示して承認を得る（`sakura-ai` スキル参照）
- 試走（スモークテスト）→ 本実行の 2 段階。本実行前に必ず止まって見積もりを再提示
- 実験条件の変更は必ず新しい run_id を切る。既存ログの上書き禁止
