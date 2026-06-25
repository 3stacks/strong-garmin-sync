"""One-time Garmin login: handles MFA once, caches SSO tokens for silent future runs.

    pip install -r requirements.txt      # (or: garminconnect, already in .venv)
    python garmin_login.py               # enter your MFA code when prompted

After this, GARMIN_TOKEN_DIR holds the tokens and nothing here (or in main.py) will
prompt again until they expire.
"""
import logging
import os
import sys

_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if os.path.exists(_VENV_PY) and os.path.normpath(sys.executable) != os.path.normpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])

from sgs.config import Config
from sgs.garmin_client import GarminClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

cfg = Config.load()
GarminClient(cfg).login()
print(f"\n✓ Garmin tokens cached at {cfg.garmin_token_dir} — future runs won't prompt for MFA.")
