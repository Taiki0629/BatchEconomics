"""データセットの読み込みと検証。正解ラベルはプロンプト側に渡さない。"""

import json
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass(frozen=True)
class Item:
    id: str
    text: str


def load_items(path: Path = config.DATASET_ITEMS) -> list[Item]:
    items = [Item(**json.loads(line)) for line in path.read_text().splitlines() if line]
    ids = [it.id for it in items]
    assert len(items) == 96, f"アイテム数が96でない: {len(items)}"
    assert len(set(ids)) == 96, "id が重複している"
    return items


def load_labels(path: Path = config.DATASET_LABELS) -> tuple[dict[str, str], dict[str, str]]:
    """(id->label, id->difficulty) を返す。"""
    labels: dict[str, str] = {}
    difficulty: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line:
            continue
        r = json.loads(line)
        labels[r["id"]] = r["label"]
        difficulty[r["id"]] = r["difficulty"]
    assert len(labels) == 96
    n_pos = sum(1 for v in labels.values() if v == "positive")
    assert n_pos == 48, f"positive が48でない: {n_pos}"
    return labels, difficulty


def load_all() -> tuple[list[Item], dict[str, str], dict[str, str]]:
    """items と labels を相互検証つきで読む。実験実行時はこちらを使うこと。

    データ差し替え（試走後の難度調整）時のミスを検出するための防護:
    - id 集合の完全一致（不一致だと採点時 KeyError → 消費後クラッシュの経路になる）
    - ラベル・難度の値域チェック（綴りミスは件数チェックでは捕まらない）
    """
    items = load_items()
    labels, difficulty = load_labels()
    item_ids = {it.id for it in items}
    assert item_ids == set(labels), "items と labels の id 集合が一致しない"
    assert set(labels.values()) <= {"positive", "negative"}, "label の値が不正"
    assert set(difficulty.values()) <= {"easy", "hard"}, "difficulty の値が不正"
    n_hard = sum(1 for v in difficulty.values() if v == "hard")
    assert n_hard == 36, f"hard が36でない: {n_hard}"
    return items, labels, difficulty
