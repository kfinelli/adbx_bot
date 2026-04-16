"""
strings.py — Load and access player-facing strings from data/strings.yaml.

Usage:
    from engine.strings import get_string
    msg = get_string("session.started")
    # Returns: "Session started. The adventure begins!"

    from engine.strings import fmt_string
    msg = fmt_string("combat.log.attack_miss", actor_name="Alice", target_name="Goblin", roll=12, target_ac=14)
    # Returns: "Alice attacks Goblin — misses! (rolled 12 vs AC 14)"

Placeholders use {key} syntax. Missing placeholders are left as-is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_strings_cache: dict[str, Any] = {}


def _data_dir() -> Path:
    return Path(__file__).parent.parent / "data"


def _load_strings() -> dict[str, Any]:
    if not _strings_cache:
        filepath = _data_dir() / "strings.yaml"
        if not filepath.exists():
            raise FileNotFoundError(f"Strings file not found: {filepath}")
        with open(filepath, encoding="utf-8") as f:
            _strings_cache.update(yaml.safe_load(f) or {})
    return _strings_cache


def get_string(key: str, default: str = "") -> str:
    """
    Look up a string by dotted key path.

    Example: get_string("session.started")
    """
    data = _load_strings()
    parts = key.split(".")
    node = data
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return str(node) if node is not None else default


def fmt_string(key: str, default: str = "", **kwargs: Any) -> str:
    """
    Look up a string and format it with the provided kwargs.

    Example: fmt_string("combat.log.attack_miss",
                         actor_name="Alice", target_name="Goblin", roll=12, target_ac=14)
    """
    template = get_string(key, default)
    if kwargs:
        try:
            template = template.format(**kwargs)
        except KeyError:
            # Missing placeholder key — leave the template as-is
            pass
    return template


def clear_strings_cache() -> None:
    """Clear the strings cache (useful for hot-reloading during dev)."""
    _strings_cache.clear()
