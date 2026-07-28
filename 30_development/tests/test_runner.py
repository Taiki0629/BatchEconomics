"""ランナーのモックテスト。API を一切叩かずにチェックポイント・予算ガード・記録の完全性を検証する。"""

import json

import pytest

from client import Attempt, RunAborted
from dataset import Item
from runner import Runner, plan_units

ITEMS = [Item(f"d-{i:03d}", f"テスト文{i}") for i in range(1, 9)]  # 8件
TRUTH = {it.id: ("positive" if i % 2 else "negative") for i, it in enumerate(ITEMS)}
PLAN = {"levels": {2: 1, 4: 1}, "planned": 12, "cap": 100}  # 8/2 + 8/4 = 6 unit × 2形式 = 12


class FakeClient:
    """常に完璧な応答を返す偽クライアント。呼び出し回数を数える。"""

    def __init__(self):
        self.calls = 0
        self.before_send = None

    def chat(self, payload, on_attempt, on_send=None):
        if self.before_send:
            self.before_send()
        if on_send:
            on_send(1)
        self.calls += 1
        user = payload["messages"][1]["content"]
        ids = [json.loads(l)["id"] for l in user.splitlines()] if not user.startswith("[") \
            else [r["id"] for r in json.loads(user)]
        body = [{"id": i, "label": TRUTH[i]} for i in ids]
        fmt_jsonl = not user.startswith("[")
        content = "\n".join(json.dumps(r) for r in body) if fmt_jsonl else json.dumps(body)
        att = Attempt(
            attempt=1, http_status=200, latency_ms=5,
            response={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )
        on_attempt(att)
        return att


class BrokenResponseClient(FakeClient):
    """choices の要素が dict でない等、壊れた 200 応答を返す。"""

    def chat(self, payload, on_attempt, on_send=None):
        if self.before_send:
            self.before_send()
        if on_send:
            on_send(1)
        self.calls += 1
        att = Attempt(attempt=1, http_status=200, latency_ms=5,
                      response={"choices": ["broken"], "usage": None})
        on_attempt(att)
        return att


def make_runner(tmp_path, client, plan=PLAN, **kw):
    defaults = dict(
        run_id="test-run", plan=plan, items=ITEMS, truth=TRUTH,
        client=client, model="fake-model", max_tokens=1000, logs_dir=tmp_path,
    )
    defaults.update(kw)
    return Runner(**defaults)


def read_logs(tmp_path):
    return [json.loads(l) for l in (tmp_path / "test-run.jsonl").open() if l.strip()]


def test_plan_units_counts_and_seed_sharing():
    units = plan_units(PLAN, ITEMS)
    assert len(units) == 12
    # 同一 trial・同一 B なら形式間でバッチ構成が一致する（シード共有）
    ja = [(t, b, i, tuple(x.id for x in batch)) for t, b, f, i, batch in units if f == "json_array"]
    jl = [(t, b, i, tuple(x.id for x in batch)) for t, b, f, i, batch in units if f == "jsonl"]
    assert sorted(ja) == sorted(jl)


def test_run_writes_sent_and_result_lines(tmp_path):
    client = FakeClient()
    r = make_runner(tmp_path, client)
    result = r.run()
    assert result["units_processed"] == 12 and client.calls == 12
    logs = read_logs(tmp_path)
    sent = [x for x in logs if x["event"] == "sent"]
    results = [x for x in logs if x["event"] == "result"]
    assert len(sent) == 12 and len(results) == 12  # write-ahead: 送信1回につき sent+result の2行
    rec = results[0]
    for field in ("finish_reason", "per_item", "correct", "output_tokens", "max_tokens"):
        assert field in rec
    assert all(x["parse_ok"] for x in results)
    assert all(x["correct"] == x["items_sent"] for x in results)
    assert results[-1]["per_item"][0]["pos"] == 0
    assert (tmp_path / "test-run.meta.json").exists()


def test_resume_skips_processed_units(tmp_path):
    c1 = FakeClient()
    make_runner(tmp_path, c1).run()
    c2 = FakeClient()
    result = make_runner(tmp_path, c2).run()
    assert c2.calls == 0  # 全 unit がチェックポイント済み → 再送信なし（二重消費なし）
    assert result["units_skipped"] == 12
    assert result["attempts_sent_total"] == 12  # sent 行から消費を正しく引き継ぐ


def test_resume_param_mismatch_aborts(tmp_path):
    make_runner(tmp_path, FakeClient()).run()
    r2 = make_runner(tmp_path, FakeClient(), max_tokens=9999)  # 条件を変えて再開しようとする
    with pytest.raises(RunAborted):
        r2.run()


def test_budget_cap_aborts(tmp_path):
    plan = dict(PLAN, cap=5)
    client = FakeClient()
    r = make_runner(tmp_path, client, plan=plan)
    with pytest.raises(RunAborted):
        r.run()
    assert client.calls == 5  # cap ちょうどで停止し、それ以上送信しない


def test_broken_200_response_still_logged(tmp_path):
    """解析不能な 200 応答でもクラッシュせず、消費記録が必ず残る。"""
    client = BrokenResponseClient()
    r = make_runner(tmp_path, client)
    r.run()
    logs = read_logs(tmp_path)
    results = [x for x in logs if x["event"] == "result"]
    assert len(results) == 12  # 全attemptがログされている（ログ喪失なし）
    # 型ガードにより解析はクラッシュせず、パース失敗として正常に記録される
    assert all(x["parse_ok"] is False for x in results)
    assert all(x["correct"] == 0 for x in results)
