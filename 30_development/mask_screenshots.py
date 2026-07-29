"""セットアップ画面のスクリーンショットを公開用に匿名化する。

元画像（`31_環境構築/`、.gitignore 済み）から、記事に載せられる形に加工して
`90_resources/setup_screenshots/` へ出力する。

加工内容:
1. 上部をクロップしてブラウザのタブ・アドレスバー・ブックマークを除去
   （URL に含まれるトークンID、開いているタブや個人のブックマークを排除する）
2. 右上の「会員ID:プロジェクト名」バッジを塗りつぶし
3. アカウントトークンID（UUID）を塗りつぶし

塗りつぶし箇所には (masked) と表示し、加工の事実が読者に分かるようにする。

使い方: uv run python 30_development/mask_screenshots.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "31_環境構築"
OUT_DIR = ROOT / "90_resources" / "setup_screenshots"

CROP_TOP = 175                          # ブラウザのクロム部分の高さ（2560x1368 のスクショ基準）
ACCOUNT_BADGE = (2140, 10, 2550, 72)    # 右上の「会員ID:プロジェクト名」
TOKEN_ID = (340, 315, 870, 380)         # アカウントトークンID の表示領域
FILL = (80, 80, 80)

SPECS = {
    "image-7": ("01_plan_selected.png", [ACCOUNT_BADGE]),
    "image-8": ("02_plan_comparison.png", [ACCOUNT_BADGE]),
    "image-9": ("03_token_and_usage.png", [ACCOUNT_BADGE, TOKEN_ID]),
}


def mask_coverage(im: Image.Image, box: tuple[int, int, int, int]) -> float:
    """指定領域が塗りつぶし色でどれだけ覆われているか（検証用）。"""
    colors = im.crop(box).getcolors(maxcolors=1_000_000) or []
    total = sum(n for n, _ in colors)
    filled = sum(n for n, px in colors if px == FILL)
    return filled / total if total else 0.0


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for src, (out_name, boxes) in SPECS.items():
        path = SRC_DIR / f"{src}.png"
        if not path.exists():
            print(f"skip: {path} が見つかりません")
            continue
        im = Image.open(path).convert("RGB")
        im = im.crop((0, CROP_TOP, im.width, im.height))
        draw = ImageDraw.Draw(im)
        for box in boxes:
            draw.rectangle(box, fill=FILL)
            draw.text((box[0] + 12, box[1] + 20), "(masked)", fill=(255, 255, 255))
        im.save(OUT_DIR / out_name, optimize=True)
        # 公開物なので、塗りつぶしが実際に効いているかを必ず検証する
        cov = [f"{mask_coverage(im, b):.1%}" for b in boxes]
        print(f"{src} -> {out_name} {im.size} マスク被覆率: {', '.join(cov)}")


if __name__ == "__main__":
    main()
