"""Configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional; env vars may be set another way
    pass

ROOT = Path(__file__).resolve().parent.parent


def _req(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise SystemExit(f"Missing required env var: {name} (see .env.example / docs/ios-capture.md)")
    return val


@dataclass
class Config:
    # --- Strong (private API; host from the one-time capture) ---
    strong_backend: str          # e.g. https://host/   (trailing slash matters: paths are joined relative)
    strong_user: str
    strong_pass: str
    strong_client_build: str     # x-client-build header, confirmed at capture
    strong_weight_unit: str      # "kg" | "lb" — confirmed at capture (step 7)

    # --- Garmin Connect ---
    garmin_user: str
    garmin_pass: str
    garmin_token_dir: Path       # SSO token cache (so we only do MFA once)

    # --- Behaviour ---
    match_window_s: int          # max |start-time delta| to pair a Strong workout with a watch activity
    lookback_days: int           # how far back to scan on each run
    state_path: Path
    dry_run: bool

    @classmethod
    def load(cls) -> "Config":
        backend = _req("STRONG_BACKEND")
        if not backend.endswith("/"):
            backend += "/"        # url-join needs the trailing slash
        unit = os.getenv("STRONG_WEIGHT_UNIT", "kg").lower()
        if unit not in ("kg", "lb"):
            raise SystemExit("STRONG_WEIGHT_UNIT must be 'kg' or 'lb'")
        return cls(
            strong_backend=backend,
            strong_user=_req("STRONG_USER"),
            strong_pass=_req("STRONG_PASS"),
            strong_client_build=os.getenv("STRONG_CLIENT_BUILD", "600013"),
            strong_weight_unit=unit,
            garmin_user=_req("GARMIN_USER"),
            garmin_pass=_req("GARMIN_PASS"),
            garmin_token_dir=Path(os.getenv("GARMIN_TOKEN_DIR", "~/.garminconnect")).expanduser(),
            match_window_s=int(os.getenv("MATCH_WINDOW_S", "5400")),   # 90 min default
            lookback_days=int(os.getenv("LOOKBACK_DAYS", "10")),
            state_path=Path(os.getenv("STATE_PATH", str(ROOT / "state.json"))).expanduser(),
            dry_run=os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes"),
        )
