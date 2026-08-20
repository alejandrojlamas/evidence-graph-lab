from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from red_privada.models import AppConfig

ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
ENV_COMPATIBILITY_ALIASES = {
    "RED_PRIVADA_USER_AGENT": "EVIDENCE_GRAPH_USER_AGENT",
}
CANONICAL_DATABASE_PATH = Path("data/state/red_privada.sqlite")
LEGACY_DATABASE_PATH = Path("data/state/evidence_graph_lab.sqlite")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if value := os.environ.get(name):
                return value
            legacy_name = ENV_COMPATIBILITY_ALIASES.get(name)
            if legacy_name and (legacy_value := os.environ.get(legacy_name)):
                return legacy_value
            return default or ""

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_config(path: str | Path) -> AppConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    config = AppConfig.model_validate(_expand_env(raw))
    configured_path = Path(config.project.database_path)
    if (
        configured_path == CANONICAL_DATABASE_PATH
        and not configured_path.exists()
        and LEGACY_DATABASE_PATH.exists()
    ):
        config.project.database_path = str(LEGACY_DATABASE_PATH)
    return config
