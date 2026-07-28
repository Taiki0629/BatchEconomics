import sys
from pathlib import Path

# 30_development はパッケージ名にできない（数字始まり）ため、パス挿入で解決する
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
