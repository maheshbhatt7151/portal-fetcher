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


def detect_portal(url: str) -> str | None:
    """Auto-detect portal name by matching URL against url_patterns in all YAML configs."""
    url_lower = url.lower()
    for name in list_configs():
        config = load_selectors(name)
        patterns = config.get("portal", {}).get("url_patterns", [])
        for pattern in patterns:
            if pattern.lower() in url_lower:
                return name
    return None


def get_probe_selectors() -> list[tuple[str, str]]:
    """Return (connector_name, css_selector) pairs for probing page structure.

    Extracts the wait_for selector from the first goto step in each connector's flow.
    These selectors are typically login form elements that uniquely identify the portal type.
    """
    probes = []
    for name in list_configs():
        config = load_selectors(name)
        flow = config.get("flow", [])
        for step in flow:
            if step.get("action") == "goto" and "wait_for" in step:
                probes.append((name, step["wait_for"]))
                break
    return probes


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
