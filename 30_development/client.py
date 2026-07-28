"""さくらのAI Engine 共通クライアント。

すべての API 呼び出しはこのクライアント経由で行う（CLAUDE.md 規約）。
リトライ・レート制御・attempt 単位の記録をここに集約する。

- リトライ対象はトランスポート層の失敗のみ: 429 / 5xx / タイムアウト（sakura-ai スキル）
- それ以外のステータス（3xx / 429以外の4xx）は即停止（リクエスト自体の誤り）
- 429 が連続したら run 全体を停止（停止条件はリトライ上限より優先）
- 消費記録の喪失防止: 送信直前に on_send（write-ahead 記録）、応答後に on_attempt を必ず呼ぶ。
  Ctrl-C 等の想定外中断でも、送信済み attempt は記録してから再送出する
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

import config


class FatalAPIError(Exception):
    """400/401 など、リトライしても無駄な失敗。即停止する。"""


class RunAborted(Exception):
    """429 連続・予算超過など、run 全体を止めるべき状況。"""


class TransportExhausted(Exception):
    """リトライ上限まで粘ったが成功しなかった。この条件を failed として先へ進む。"""


@dataclass
class Attempt:
    """1回の送信の記録。budget 集計の最小単位（失敗もカウントする）。"""

    attempt: int                 # 1始まり
    http_status: int | None      # タイムアウト等で応答なしなら None
    latency_ms: int
    error: str | None = None
    retry_after: str | None = None
    response: dict[str, Any] | None = None
    error_body: str | None = None   # 非200時のボディ先頭（原因調査用）


@dataclass
class SakuraClient:
    api_key: str
    base_url: str = config.API_BASE
    min_interval: float = config.MIN_INTERVAL_SEC
    timeout: float = config.TIMEOUT_SEC
    retry_max: int = config.RETRY_MAX
    # 送信直前に呼ばれるフック。予算ガードはここで RunAborted を投げる
    before_send: Callable[[], None] | None = None
    _last_send_at: float = field(default=0.0, init=False)
    _consecutive_429: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _wait_interval(self) -> None:
        # リクエスト間の最低間隔を保つ（レート制限閾値が非公開のため安全側）
        elapsed = time.monotonic() - self._last_send_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def chat(
        self,
        payload: dict[str, Any],
        on_attempt: Callable[[Attempt], None],
        on_send: Callable[[int], None] | None = None,
    ) -> Attempt:
        """chat/completions を1回呼ぶ（トランスポート失敗時はリトライ）。

        on_send は各 attempt の送信直前に呼ばれる（write-ahead 記録用。
        送信後にプロセスが落ちても消費の痕跡が残る）。
        on_attempt は各 attempt の結果を通知する（成功・失敗とも枠を消費するため）。
        戻り値は HTTP 200 の attempt（ボディ破損時は response=None のまま返す = コンテンツ失敗、D-008）。
        """
        backoff = config.BACKOFF_BASE_SEC
        for n in range(1, self.retry_max + 2):  # 初回 + リトライ上限
            if self.before_send is not None:
                self.before_send()
            self._wait_interval()
            if on_send is not None:
                on_send(n)
            start = time.monotonic()
            self._last_send_at = start
            status: int | None = None
            error: str | None = None
            retry_after: str | None = None
            body: dict[str, Any] | None = None
            error_body: str | None = None
            try:
                resp = self._http.post(config.CHAT_ENDPOINT, json=payload)
                status = resp.status_code
                retry_after = resp.headers.get("Retry-After")
                if status == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        # 200 なのにボディが JSON でない。消費済みなので必ず記録する
                        error = "json_decode_200"
                else:
                    error = f"http_{status}"
                    try:
                        error_body = resp.text[:500]
                    except Exception:
                        pass
            except httpx.TimeoutException:
                error = "timeout"
            except httpx.HTTPError as e:
                error = f"transport_{type(e).__name__}"
            except BaseException as e:
                # Ctrl-C 等の想定外中断。送信済みの可能性があるため記録してから再送出
                latency_ms = int((time.monotonic() - start) * 1000)
                on_attempt(
                    Attempt(
                        attempt=n,
                        http_status=None,
                        latency_ms=latency_ms,
                        error=f"aborted_{type(e).__name__}",
                    )
                )
                raise
            latency_ms = int((time.monotonic() - start) * 1000)

            att = Attempt(
                attempt=n,
                http_status=status,
                latency_ms=latency_ms,
                error=error,
                retry_after=retry_after,
                response=body,
                error_body=error_body,
            )
            on_attempt(att)

            if status == 200:
                self._consecutive_429 = 0
                return att   # body 破損(json_decode_200)もリトライしない（コンテンツ失敗扱い）

            if status == 429:
                self._consecutive_429 += 1
                if self._consecutive_429 >= config.CONSECUTIVE_429_ABORT:
                    raise RunAborted(
                        f"429 が {self._consecutive_429} 連続。無償枠超過のレート制御の可能性。"
                        " /budget で消費を確認してください。"
                    )
            else:
                self._consecutive_429 = 0

            # リトライ対象は 429 / 5xx / 応答なし（タイムアウト・トランスポート）のみ
            retryable = (
                status == 429
                or (status is not None and 500 <= status < 600)
                or status is None
            )
            if not retryable:
                raise FatalAPIError(
                    f"HTTP {status}: リクエスト自体の誤りの可能性。即停止。 body={error_body}"
                )

            if n >= self.retry_max + 1:
                break
            # 待機: Retry-After（数値）があればそれに従う。なければ指数バックオフ
            if retry_after is not None:
                try:
                    wait = min(float(retry_after), config.RETRY_AFTER_CAP_SEC)
                except ValueError:
                    # HTTP-date 形式は未対応。バックオフで代用
                    wait = min(backoff, config.BACKOFF_CAP_SEC)
            else:
                wait = min(backoff, config.BACKOFF_CAP_SEC)
            time.sleep(wait)
            backoff = min(backoff * 2, config.BACKOFF_CAP_SEC)

        raise TransportExhausted(f"リトライ上限 {self.retry_max} 回に到達。")
