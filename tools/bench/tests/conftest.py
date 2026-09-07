import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for folder in (REPO / "tools/bench", REPO / "backend", REPO / "desktop"):
    sys.path.insert(0, str(folder))
