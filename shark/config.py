"""Loads config.json and layers in API credentials from the environment.

API keys are never stored in config.json - only SHARK_API_KEY /
SHARK_API_SECRET env vars, same rule as before the refactor. A .env file
in the project root (see .env.example) is loaded automatically if
present - real shell env vars still take priority over it.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    config["api_key"] = os.environ.get("SHARK_API_KEY", "")
    config["api_secret"] = os.environ.get("SHARK_API_SECRET", "")
    return config


def strategy_params(config: dict, strategy_name: str) -> dict:
    try:
        return config["strategies"][strategy_name]
    except KeyError:
        raise KeyError(
            f"No config found for strategy '{strategy_name}' under 'strategies' in config.json"
        )
