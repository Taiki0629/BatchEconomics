"""プロンプト構築・パース・採点のテスト（API を叩かない）。"""

import json

from dataset import Item
from prompts import build_messages, parse_output, score_batch

BATCH = [Item("d-001", "最高でした。"), Item("d-002", "最悪でした。")]
TRUTH = {"d-001": "positive", "d-002": "negative"}


def test_build_messages_contains_ids_not_labels():
    for fmt in ("json_array", "jsonl"):
        msgs = build_messages(BATCH, fmt)
        assert msgs[0]["role"] == "system"
        user = msgs[1]["content"]
        assert "d-001" in user and "d-002" in user
        assert "positive" not in user  # 正解ラベルの混入禁止（指示文はsystem側）


def test_parse_json_array_clean():
    text = '[{"id":"d-001","label":"positive"},{"id":"d-002","label":"negative"}]'
    p = parse_output(text, "json_array")
    assert p.parse_ok and len(p.records) == 2 and not p.fence_stripped


def test_parse_json_array_fenced():
    text = '```json\n[{"id":"d-001","label":"positive"}]\n```'
    p = parse_output(text, "json_array")
    assert p.parse_ok and p.fence_stripped and len(p.records) == 1


def test_parse_json_array_broken_is_total_loss():
    p = parse_output('[{"id":"d-001","label":"posi', "json_array")
    assert not p.parse_ok and p.records == []


def test_parse_json_array_non_dict_elements_counted():
    p = parse_output('[{"id":"d-001","label":"positive"}, "ゴミ要素"]', "json_array")
    assert p.parse_ok and len(p.records) == 1 and p.broken_lines == 1


def test_parse_jsonl_partial_recovery():
    text = '{"id":"d-001","label":"positive"}\n{"id":"d-002","labe←壊れ\n'
    p = parse_output(text, "jsonl")
    assert p.parse_ok  # 1行以上復元できた
    assert len(p.records) == 1 and p.broken_lines == 1


def test_parse_jsonl_all_broken():
    p = parse_output("完全に壊れた出力\nこれも壊れ", "jsonl")
    assert not p.parse_ok and p.broken_lines == 2


def test_score_perfect():
    recs = [
        {"id": "d-001", "label": "positive"},
        {"id": "d-002", "label": "negative"},
    ]
    s = score_batch(BATCH, recs, TRUTH)
    assert s.correct == 2 and s.id_match_rate == 1.0
    assert s.per_item[0]["pos"] == 0 and s.per_item[1]["pos"] == 1


def test_score_label_normalization():
    recs = [{"id": "d-001", "label": " Positive "}]
    s = score_batch(BATCH, recs, TRUTH)
    assert s.per_item[0]["correct"] is True


def test_score_dup_id_first_wins():
    recs = [
        {"id": "d-001", "label": "negative"},  # 最初の1件が採用される（不正解）
        {"id": "d-001", "label": "positive"},
    ]
    s = score_batch(BATCH, recs, TRUTH)
    assert s.dup_id_count == 1 and s.correct == 0


def test_score_unknown_id_ignored():
    recs = [{"id": "d-999", "label": "positive"}]
    s = score_batch(BATCH, recs, TRUTH)
    assert s.unknown_id_count == 1 and s.id_match_rate == 0.0 and s.correct == 0


def test_score_missing_item_incorrect():
    recs = [{"id": "d-001", "label": "positive"}]
    s = score_batch(BATCH, recs, TRUTH)
    assert s.correct == 1
    assert s.per_item[1]["predicted"] is None and s.per_item[1]["correct"] is False
