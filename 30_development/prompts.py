"""プロンプト2形式（json_array / jsonl）の構築・パース・採点。

実験設計 §1.2〜1.3 の定義に厳密に従う:
- 指示文は共通テンプレートで、形式指定部分だけ差し替える（形式間の公平性）
- json_array: 全体を json.loads。コードフェンスは剥がしてから（剥がした事実は記録）
- jsonl: 行ごとに json.loads。壊れた行があっても他の行は回収（部分回収を記録）
- 採点: 同一idの複数返却は最初の1件のみ採用(dup_id_count)、未送信idは無視(unknown_id_count)
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from dataset import Item

FORMATS = ("json_array", "jsonl")

_SYSTEM_COMMON = (
    "あなたは日本語レビューの感情分類器です。"
    "与えられた各レビューの全体的な極性を positive か negative のどちらかに分類してください。\n"
    "ルール:\n"
    "- 入力に含まれる id をそのまま返すこと\n"
    "- すべてのアイテムについて必ず1件ずつ出力すること\n"
    "- label の値は positive または negative のみ\n"
    "- 説明文・前置き・コードフェンスを出力しないこと\n"
)

_FORMAT_INSTRUCTION = {
    "json_array": (
        "出力形式: 入力と同数の要素を持つ JSON 配列だけを出力する。\n"
        '各要素は {"id": "<入力のid>", "label": "positive|negative"} とする。'
    ),
    "jsonl": (
        "出力形式: 1行につき1アイテムの JSON オブジェクトだけを出力する。\n"
        '各行は {"id": "<入力のid>", "label": "positive|negative"} とする。'
    ),
}


def build_messages(batch: list[Item], fmt: str) -> list[dict[str, str]]:
    assert fmt in FORMATS
    if fmt == "json_array":
        user = json.dumps(
            [{"id": it.id, "text": it.text} for it in batch], ensure_ascii=False
        )
    else:
        user = "\n".join(
            json.dumps({"id": it.id, "text": it.text}, ensure_ascii=False)
            for it in batch
        )
    return [
        {"role": "system", "content": _SYSTEM_COMMON + _FORMAT_INSTRUCTION[fmt]},
        {"role": "user", "content": user},
    ]


_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.S)


def strip_fence(text: str) -> tuple[str, bool]:
    """コードフェンスで全体が包まれていたら剥がす。剥がしたかどうかも返す。"""
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1), True
    return text, False


@dataclass
class ParseResult:
    parse_ok: bool
    fence_stripped: bool = False
    broken_lines: int = 0            # jsonl のみ: json.loads に失敗した行数
    records: list[dict[str, Any]] = field(default_factory=list)


def parse_output(text: str, fmt: str) -> ParseResult:
    body, stripped = strip_fence(text or "")
    if fmt == "json_array":
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ParseResult(parse_ok=False, fence_stripped=stripped)
        if not isinstance(data, list):
            return ParseResult(parse_ok=False, fence_stripped=stripped)
        records = [r for r in data if isinstance(r, dict)]
        # dict 以外の要素は jsonl の破損行に相当するものとして計上する（観察の対称性）
        return ParseResult(
            parse_ok=True,
            fence_stripped=stripped,
            broken_lines=len(data) - len(records),
            records=records,
        )
    # jsonl: 行単位で復元。1行以上復元できれば parse_ok（全滅検知用）
    records: list[dict[str, Any]] = []
    broken = 0
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(r, dict):
            records.append(r)
        else:
            broken += 1
    return ParseResult(
        parse_ok=len(records) > 0,
        fence_stripped=stripped,
        broken_lines=broken,
        records=records,
    )


@dataclass
class Score:
    items_returned: int
    id_match_rate: float
    dup_id_count: int
    unknown_id_count: int
    correct: int
    per_item: list[dict[str, Any]]


def score_batch(
    batch: list[Item], records: list[dict[str, Any]], truth: dict[str, str]
) -> Score:
    sent_ids = {it.id for it in batch}
    predicted: dict[str, str] = {}
    dup = 0
    unknown = 0
    for r in records:
        rid = str(r.get("id", "")).strip()
        label = str(r.get("label", "")).strip().lower()
        if rid not in sent_ids:
            unknown += 1
            continue
        if rid in predicted:
            dup += 1          # 同一idの複数返却: 最初の1件のみ採用
            continue
        predicted[rid] = label
    per_item = []
    correct = 0
    for pos, it in enumerate(batch):
        pred = predicted.get(it.id)
        ok = pred == truth[it.id]
        correct += int(ok)
        per_item.append(
            {
                "pos": pos,
                "item_id": it.id,
                "expected": truth[it.id],
                "predicted": pred,
                "correct": ok,
            }
        )
    return Score(
        items_returned=len(records),
        id_match_rate=len(predicted) / len(batch) if batch else 0.0,
        dup_id_count=dup,
        unknown_id_count=unknown,
        correct=correct,
        per_item=per_item,
    )
