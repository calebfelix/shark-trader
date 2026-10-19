"""Machine-local settings, kept in the gitignored local_settings.json so
they can never be committed. Currently just the trading mode override
written by the dashboard's Settings page. config.json's "dry_run" stays
true in git; shark/config.py applies this override on top when present.
SHARK_LOCAL_SETTINGS_FILE overrides the path (used by tests).
"""

import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "local_settings.json"


def _path() -> Path:
    return Path(os.environ.get("SHARK_LOCAL_SETTINGS_FILE") or DEFAULT_PATH)


def get_dry_run_override() -> Optional[bool]:
    """True/False if a mode was saved, None if the config.json value applies."""
    try:
        value = json.loads(_path().read_text(encoding="utf-8")).get("dry_run")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, bool) else None


def set_dry_run(dry_run: bool) -> None:
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"dry_run": bool(dry_run)}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
