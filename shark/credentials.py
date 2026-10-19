"""API credentials saved as credentials.json next to the code, on whichever
machine the bot/dashboard runs. The file is gitignored so it never reaches
the repo. The dashboard's Settings page writes it; shark/config.py reads it
first, then falls back to the SHARK_API_KEY / SHARK_API_SECRET env vars.

The file is plain text - protect the machine/folder it lives on.
SHARK_CREDENTIALS_FILE overrides the path (used by tests).
"""

import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "credentials.json"


def _path() -> Path:
    return Path(os.environ.get("SHARK_CREDENTIALS_FILE") or DEFAULT_PATH)


def get_credentials() -> tuple[str, str]:
    """(api_key, api_secret) from credentials.json, or ("", "") if the
    file is missing or unreadable."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return str(data.get("api_key", "")), str(data.get("api_secret", ""))
    except (OSError, ValueError):
        return "", ""


def set_credentials(api_key: str, api_secret: str) -> None:
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"api_key": api_key, "api_secret": api_secret}, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def clear_credentials() -> None:
    try:
        _path().unlink()
    except FileNotFoundError:
        pass


def key_hint(api_key: str) -> str:
    """Last 4 characters only - enough to recognise which key is saved."""
    return "…" + api_key[-4:] if len(api_key) >= 8 else ""
