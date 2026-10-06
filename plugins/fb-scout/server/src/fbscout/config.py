"""Paths and settings. Everything can be overridden with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# Browser channels tried in order. "chrome" = installed Google Chrome,
# "msedge" = Microsoft Edge (always present on Windows),
# "chromium" = Playwright's bundled browser (needs `playwright install chromium`).
DEFAULT_CHANNELS = ("chrome", "msedge", "chromium")

VIEWPORT = {"width": 1280, "height": 900}


def home_dir() -> Path:
    """Where the browser profile (your Facebook login) lives. Never inside the code folder."""
    return Path(os.environ.get("FBSCOUT_HOME") or Path.home() / ".fbscout")


def profile_dir(channel: str) -> Path:
    return home_dir() / f"profile-{channel}"


def default_output_root() -> Path:
    """Results go to FBSCOUT_OUTPUT_DIR, else <project>/fb-scout-output."""
    if os.environ.get("FBSCOUT_OUTPUT_DIR"):
        return Path(os.environ["FBSCOUT_OUTPUT_DIR"])
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(base) / "fb-scout-output"


def dataset_path(output_root: Path | str | None = None) -> Path:
    """The SQLite dataset: FBSCOUT_DB, else <output root>/fbscout.sqlite (one dataset per output folder)."""
    if os.environ.get("FBSCOUT_DB"):
        return Path(os.environ["FBSCOUT_DB"])
    return Path(output_root if output_root is not None else default_output_root()) / "fbscout.sqlite"


def anonymization_salt() -> bytes:
    """Secret salt for author pseudonyms. Kept with the login (not with the data), so
    published pseudonyms can't be reversed by hashing a list of known names."""
    path = home_dir() / "anon_salt"
    try:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(32)
        path.write_text(salt.hex(), encoding="ascii")
        return salt


def browser_channels() -> tuple[str, ...]:
    forced = (os.environ.get("FBSCOUT_BROWSER") or "").strip().lower()
    return (forced,) if forced else DEFAULT_CHANNELS


def pace_factor() -> float:
    """Multiplier for all human-like delays (1.0 = normal, 0 = no delays, for tests)."""
    try:
        return max(0.0, float(os.environ.get("FBSCOUT_PACE", "1")))
    except ValueError:
        return 1.0
