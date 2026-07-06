"""Local preview launcher — pins offline/stub env and a deterministic 'today' so return
windows are meaningful, then serves the app. Used by .claude/launch.json for browser preview.
Not part of the product; .claude/ is gitignored.
"""

import os
import sys

# running `python scripts/_preview_serve.py` puts scripts/ on sys.path, not the repo root
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

os.environ.setdefault("LLM_PROVIDER", "stub")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("AS_OF_DATE", "2026-06-22")  # == db.dataset.REFERENCE_DATE
os.environ.setdefault("RG_SEED_DEMO", "1")  # keep the demo tenant available locally too
os.environ.setdefault("RG_DRAW_DELAY_S", "120")  # lottery tickets draw in 2 min (prod default: 6 PM)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("service.app:app", host="127.0.0.1", port=8000, log_level="info")
