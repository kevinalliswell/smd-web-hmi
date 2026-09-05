"""Export the current backend HTTP schema without running the application lifespan."""
from __future__ import annotations
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'backend'))
from app.main import app  # noqa: E402

target = root / 'frontend/src/api/generated/openapi.json'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
