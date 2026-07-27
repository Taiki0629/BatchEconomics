# 公式ドキュメント出典メモ

確認日: 2026-07-27（すべて WebFetch / curl で一次情報を直接確認）

| 情報 | 出典 URL |
|---|---|
| マニュアル目次 | https://manual.sakura.ad.jp/cloud/manual-ai-engine.html |
| サービス基本情報（SLA 対象外、RAG 課金注意） | https://manual.sakura.ad.jp/cloud/ai-engine/01-basics.html |
| 利用手順（エンドポイント、認証、トークン発行手順、無償枠とレートリミット） | https://manual.sakura.ad.jp/cloud/ai-engine/02-howto.html |
| 操作ガイド（モデル確認方法、パラメータ例） | https://manual.sakura.ad.jp/cloud/ai-engine/03-operation-guide.html |
| サービスサイト（モデル一覧、無償枠数値、単価、プラン仕様） | https://ai.sakura.ad.jp/sakura-ai/ai-engine/ |
| API ポータル（Redoc SPA） | https://manual.sakura.ad.jp/api/cloud/portal/?api=ai-engine-inference-api |
| Inference API の OpenAPI 仕様（本体） | https://manual.sakura.ad.jp/api/cloud/portal/assets/ai-engine-inference-CV9_pCOk.yaml |

## ローカル控え

- `ai-engine-inference-openapi.yaml` — Inference API の OpenAPI 3.0 仕様（1,074 行）。エンドポイント 8 本、BearerAuth、chat/completions のパラメータ（model, messages, max_tokens, temperature, stream, tools, tool_choice）、エラーコード（400/401/429/500/504）の一次情報

## 注意

- OpenAPI 仕様のアセット URL はビルドハッシュ付きのため、リンク切れ時は API ポータルの JS バンドルから再取得する
- 確認済み事実と要確認事項の整理は `.claude/skills/sakura-ai/SKILL.md` 参照
