"""Loads config.json and layers in API credentials.

API keys are never stored in config.json. They come from the gitignored
credentials.json (written by the dashboard's Settings page, see
shark/credentials.py), falling back to the SHARK_API_KEY / SHARK_API_SECRET
env vars. A .env file in the project root (see .env.example) is loaded
automatically if present - real shell env vars still take priority over it.
"""

import json
import os

from dotenv import load_dotenv

from .credentials import get_credentials
from .local_settings import get_dry_run_override

load_dotenv()


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    override = get_dry_run_override()
    if override is not None:
        config["dry_run"] = override
    saved_key, saved_secret = get_credentials()
    config["api_key"] = saved_key or os.environ.get("SHARK_API_KEY", "")
    config["api_secret"] = saved_secret or os.environ.get("SHARK_API_SECRET", "")
    return config


def strategy_params(config: dict, strategy_name: str) -> dict:
    try:
        return config["strategies"][strategy_name]
    except KeyError:
        raise KeyError(
            f"No config found for strategy '{strategy_name}' under 'strategies' in config.json"
        )
