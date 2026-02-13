"""Adapter registry — maps portal names to adapter classes."""

from __future__ import annotations

from typing import Any, Dict, Tuple, Type

from portal_fetcher.adapters.base import BasePortalAdapter
from portal_fetcher.adapters.h8_onebroadband import H8OneBroadbandAdapter

# Hardcoded adapter registry (fallback)
ADAPTER_REGISTRY: Dict[str, Type[BasePortalAdapter]] = {
    "h8_onebroadband": H8OneBroadbandAdapter,
}


def get_adapter(name: str) -> Tuple[Type[BasePortalAdapter], dict[str, Any]]:
    """Look up an adapter class by portal name.

    Returns (adapter_class, extra_kwargs) tuple.
    Prefers YAML-driven ConfigDrivenAdapter when a selector config exists;
    falls back to the hardcoded adapter.
    """
    from portal_fetcher.selector_store import has_config, load_selectors

    # YAML-first: if a selector config exists, use ConfigDrivenAdapter
    if has_config(name):
        from portal_fetcher.adapters.config_driven import ConfigDrivenAdapter

        config = load_selectors(name)
        return ConfigDrivenAdapter, {"selector_config": config}

    # Fallback to hardcoded adapter
    if name not in ADAPTER_REGISTRY:
        available = ", ".join(sorted(ADAPTER_REGISTRY))
        raise KeyError(f"Unknown portal '{name}'. Available: {available}")
    return ADAPTER_REGISTRY[name], {}


def detect_and_get_adapter(url: str) -> Tuple[str, Tuple[Type[BasePortalAdapter], dict[str, Any]]]:
    """Auto-detect portal from URL and return (portal_name, (adapter_cls, kwargs)).

    Raises KeyError if no portal matches the URL.
    """
    from portal_fetcher.selector_store import detect_portal

    name = detect_portal(url)
    if not name:
        raise KeyError(
            f"No portal connector found for URL '{url}'. "
            "Please select a portal manually or add a YAML config with matching url_patterns."
        )
    return name, get_adapter(name)
