"""YAML selector config loader and accessor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SELECTORS_DIR = Path(__file__).parent / "selectors"


def list_configs() -> list[str]:
    """Return names of all available YAML selector configs."""
    return [p.stem for p in SELECTORS_DIR.glob("*.yaml")]


def has_config(name: str) -> bool:
    """Check if a YAML config exists for the given portal name."""
    return (SELECTORS_DIR / f"{name}.yaml").exists()


def load_selectors(name: str) -> dict[str, Any]:
    """Load a YAML selector config by portal name."""
    path = SELECTORS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No selector config for '{name}' at {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def save_selectors(name: str, config: dict[str, Any]) -> Path:
    """Save a selector config to YAML."""
    path = SELECTORS_DIR / f"{name}.yaml"
    SELECTORS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    return path


def get_selector(config: dict[str, Any], dotted_key: str) -> str | None:
    """Resolve a dotted key like 'login.username_field' from a nested config dict.

    Returns None if any part of the path is missing.
    """
    parts = dotted_key.split(".")
    node: Any = config
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def get_selector_list(config: dict[str, Any], dotted_key: str) -> list[str]:
    """Resolve a dotted key to a list of strings (e.g., popup_dismiss buttons)."""
    parts = dotted_key.split(".")
    node: Any = config
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return []
        node = node[part]
    if isinstance(node, list):
        return [str(item) for item in node]
    return []
