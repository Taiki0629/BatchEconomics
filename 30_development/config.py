"""プロジェクト共通設定。APIキーは必ず環境変数(.env)から読む。"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT / "40_test" / "logs"
DATASET_ITEMS = ROOT / "90_resources" / "dataset" / "sentiment_96.jsonl"
DATASET_LABELS = ROOT / "90_resources" / "dataset" / "sentiment_96_labels.jsonl"

API_BASE = "https://api.ai.sakura.ad.jp"
CHAT_ENDPOINT = "/v1/chat/completions"

MODEL = "gpt-oss-120b"          # 第一候補。試走で利用不可なら llm-jp-3.1-8x13b-instruct4 へ (D-006)
TEMPERATURE = 0.0
# max_tokens は全水準共通の固定値（B比例にすると切断がBと交絡する。実験設計 §1）。
# 下記は校正前の仮値。試走 p3-001 の実測最大出力トークン×3 で確定させること。
MAX_TOKENS_PROVISIONAL = 8192

MIN_INTERVAL_SEC = 1.0          # リクエスト間の最低間隔（レート制限閾値が非公開のため安全側）
TIMEOUT_SEC = 180.0
RETRY_MAX = 5                   # トランスポート失敗(429/5xx/タイムアウト)のリトライ上限
BACKOFF_BASE_SEC = 2.0          # 指数バックオフ初期値（2,4,8,...、上限60秒）
BACKOFF_CAP_SEC = 60.0
RETRY_AFTER_CAP_SEC = 300.0     # Retry-Afterヘッダーに従う際の待機上限
CONSECUTIVE_429_ABORT = 3       # 429がこの回数連続したらrun全体を停止（リトライ上限より優先）

BASE_SEED = 42


def load_api_key() -> str:
    """SAKURA_API_KEY を .env から読む。未設定なら明確に失敗させる。"""
    load_dotenv(ROOT / ".env")
    key = os.environ.get("SAKURA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "SAKURA_API_KEY が未設定です。.env を確認してください（U-001）。"
        )
    return key
