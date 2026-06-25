"""Validate the Strong half after the capture: log in with the captured host + your
creds, fetch recent workouts, print them. Needs only STRONG_* env (no Garmin/FIT deps).

    pip install requests python-dateutil python-dotenv
    python probe_strong.py

Paste me the output — especially any "Strong cellType seen:" lines and whether the
weights look like kg or lb — and I'll finalise the parser + lock the weight unit.
"""
from __future__ import annotations

import os
import sys

_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if os.path.exists(_VENV_PY) and os.path.normpath(sys.executable) != os.path.normpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])

import logging
from pathlib import Path
from types import SimpleNamespace

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sgs.strong_client import StrongClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("sgs.strong").setLevel(logging.DEBUG)   # surfaces real cellType strings
logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    backend = os.environ.get("STRONG_BACKEND")
    if not backend:
        raise SystemExit("Set STRONG_BACKEND (and STRONG_USER/STRONG_PASS) in .env first.")
    if not backend.endswith("/"):
        backend += "/"
    cfg = SimpleNamespace(
        strong_backend=backend,
        strong_user=os.environ["STRONG_USER"],
        strong_pass=os.environ["STRONG_PASS"],
        strong_client_build=os.getenv("STRONG_CLIENT_BUILD", "600013"),
        strong_weight_unit=os.getenv("STRONG_WEIGHT_UNIT", "kg"),
        state_path=Path("state.json"),
    )
    client = StrongClient(cfg)
    workouts = client.fetch_workouts(limit=10, max_pages=1)

    print(f"\n=== Fetched {len(workouts)} workout(s); showing up to 3 ===")
    for w in workouts[:3]:
        print(w)
        for ex in w.exercises[:6]:
            sets = ", ".join(
                f"{s.reps}x{s.weight if s.weight is not None else '—'}" for s in ex.sets
            )
            print(f"    {ex.name}: {sets}")
    if workouts and not any(w.exercises for w in workouts):
        print("\n(!) Workouts fetched but no sets parsed — check the 'cellType seen' lines "
              "above; my cellType guesses likely need adjusting. Paste them to me.")


if __name__ == "__main__":
    main()
