# タスクリスト

最終更新: 2026-07-27 15:45

## 🙋 あなたの作業待ち

> ここに何か入っている間、Claude Code は先に進めません。

| ID | やってほしいこと | なぜ必要か | 手順 | 完了条件 |
|----|-----------------|-----------|------|---------|
| U-001 | さくらのAI Engine のアカウントトークンを発行し `.env` に設定 | API を叩けないため P3 以降の全実験がブロック | 1. `https://secure.sakura.ad.jp/ai/` にログイン 2. 利用規約に同意しプランを選択（**基盤モデル無償プラン推奨**。超過時も課金されずレート制限のみ） 3. 左メニュー「アカウントトークン」→ 右上「アカウントトークンを作成」→ トークン名（例: `batch-economics`）を入力して作成 4. プロジェクトルートで `cp .env.example .env` し、`SAKURA_API_KEY=<UUID>:<シークレット>` を貼り付け | `.env` に `SAKURA_API_KEY` が入っている（P3 開始までに必要。R-00 承認は先行可） |
| U-002 | （R-00 での判断後・採用時のみ）MCP サーバー追加と Claude Code 再起動 | MCP の追加と再起動は taiki さんにしかできない | R-00 の「判断してほしいこと」参照。推奨は「追加なし」のため、採用ならこのタスクは削除。追加する場合は本プロジェクトディレクトリで `claude mcp add sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking` を実行し Claude Code を再起動 | R-00 の判断が確定し、必要なら MCP が `claude mcp list` に表示される |

## 📍 現在地

- 進行中フェーズ: Phase 0 環境セットアップ
- 次のレビューゲート: **R-00（レビュー依頼中）**
- ブロッカー: なし（U-001 は P3 開始までに必要。R-00 承認は先行できる）

## フェーズ進捗

| Phase | 内容 | 状態 | レビュー | 承認日 |
|---|---|---|---|---|
| P0 | 環境セットアップ | 👀 | R-00 | - |
| P1 | 背景・課題定義 | ⬜ | R-01 | - |
| P2 | タスクリスト化・実験設計 | ⬜ | R-02 | - |
| P3 | 実装・実験実行 | ⬜ | R-03 | - |
| P4 | 考察 | ⬜ | R-04 | - |
| P5 | Output作成 | ⬜ | R-05 | - |

## 全タスク

| ID | Phase | タスク | 作業者 | 状態 | 成果物 | 備考 |
|----|-------|--------|--------|------|--------|------|
| T-001 | P0 | 作業ディレクトリ・既存ファイル確認 | 🤖 claude | ✅ | - | 全フォルダ空を確認 |
| T-002 | P0 | `CLAUDE.md`（プロジェクト憲法）作成 | 🤖 claude | ✅ | `CLAUDE.md` | |
| T-003 | P0 | 管理ファイル作成 | 🤖 claude | ✅ | `TASKS.md` `REVIEW_LOG.md` `DECISIONS.md` | |
| T-004 | P0 | `.claude/` 設定・コマンド・スキル作成 | 🤖 claude | ✅ | `.claude/` 一式 | |
| T-005 | P0 | さくらのAI Engine 公式ドキュメント調査 | 🤖 claude | ✅ | `90_resources/official_docs/` | OpenAPI 仕様を保存 |
| T-006 | P0 | MCP 現状確認・要否判断 | 🤖 claude | ✅ | `DECISIONS.md` D-001 | 推奨: 追加なし |
| T-007 | P0 | `.env.example` `.gitignore` `README.md` 作成 | 🤖 claude | ✅ | ルート直下 | |
| U-001 | P0 | アカウントトークン発行 → `.env` 設定 | 🙋 taiki | ⏳ | `.env` | P3 開始までに必要 |
| U-002 | P0 | MCP 追加 + 再起動（採用時のみ） | 🙋 taiki | ⏸ | - | R-00 判断待ち |
| T-101 | P1 | 背景・課題定義・仮説 H1〜H6 の整理 | 🤖 claude | ⬜ | `20_design/01_background.md` | R-00 承認後 |
| T-201 | P2 | 実験設計・消費リクエスト見積もり | 🤖 claude | ⬜ | `20_design/02_experiment_design.md` | R-01 承認後 |
| T-301 | P3 | uv で Python 3.12 環境構築 | 🤖 claude | ⬜ | `30_development/` | R-02 承認後 |
| T-302 | P3 | 共通クライアント・実験ランナー実装 | 🤖 claude | ⬜ | `30_development/` | |
| T-303 | P3 | 小規模試走 → 本実行 | 🤖 claude | ⬜ | `40_test/logs/` | 本実行前に見積もり再提示 |
| T-401 | P4 | 集計・可視化・考察 | 🤖 claude | ⬜ | `40_test/analysis/` | |
| T-501 | P5 | Qiita 原稿・公開リポジトリ整形 | 🤖 claude | ⬜ | `50_output/` | gh CLI 認証済み |

---
### 凡例
- 作業者: 🤖 claude / 🙋 taiki
- 状態: ⬜未着手 / 🔄進行中 / ⏳あなた待ち / 👀レビュー待ち / ✅完了 / ❌中止 / ⏸保留
