"""Project configuration — settings.json + resolved paths.

Secrets never live here: SMTP is read from an external, git-ignored ini (reused
from the Zara parser) and/or environment variables. This module only resolves
the settings file and turns relative paths into absolute ones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 so logging an accented brand name never crashes.

    Windows scheduled tasks launch Python with a legacy cp1252 console; a plain
    ``print()`` of a name like ``Hermès`` or a cache key with accents then raises
    ``UnicodeEncodeError`` and takes down the whole run — this is exactly what
    stopped the nightly fetch on 2026-11-10 (a GDELT give-up message and a signals
    skip message, both printing accented text). Reconfiguring the streams to UTF-8
    with a non-fatal error handler makes logging robust regardless of the launch
    locale. Idempotent, and a no-op when the streams are already UTF-8 or lack
    ``reconfigure`` (e.g. captured during tests)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULTS = {
    "recipients": [],
    "top_n": 500,
    "shard_count": 28,
    "do_trends": True,
    "subject_prefix": "VGR Brand Search Interest",
    "smtp_ini": "",
}


def load_settings() -> dict:
    cfg = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        cfg.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    # resolve the external SMTP ini path relative to the project root
    ini = cfg.get("smtp_ini") or ""
    if ini:
        p = Path(ini)
        cfg["smtp_ini"] = str(p if p.is_absolute() else (ROOT / p).resolve())
    return cfg
