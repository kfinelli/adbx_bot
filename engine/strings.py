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

# Discord character limits by key suffix.
# Applied to every leaf string whose dotted key ends with the given suffix.
_SUFFIX_LIMITS: dict[str, int] = {
    "placeholder": 100,   # TextInput / Select placeholder
    "description": 100,   # Used as TextInput placeholder in modals
    "label":        80,   # Button label (TextInput labels are stricter — see _KEY_LIMITS)
}

# Per-key overrides: modal TextInput labels are capped at 45 chars, stricter than buttons.
_KEY_LIMITS: dict[str, int] = {
    "ui.search.label":       45,
    "ui.disarm.label":       45,
    "ui.listen.label":       45,
    "ui.force_door.label":   45,
    "ui.pick_lock.label":    45,
    "ui.craft.label":        45,
    "ui.other.action_label": 45,
    "ui.affect.label":       45,
    "ui.oracle.label":       45,
    "ui.emote.label":        45,
    "ui.say.label":          45,
}


def _collect_violations(node: dict[str, Any], prefix: str = "") -> list[str]:
    """Walk all leaf strings and return a list of limit-violation messages."""
    violations: list[str] = []
    for key, value in node.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            violations.extend(_collect_violations(value, full_key))
        elif isinstance(value, str):
            suffix = full_key.rsplit(".", 1)[-1]
            limit = _KEY_LIMITS.get(full_key) or _SUFFIX_LIMITS.get(suffix)
            if limit and len(value) > limit:
                violations.append(
                    f"  {full_key}: {len(value)} chars (max {limit})\n"
                    f"    {value!r}"
                )
    return violations

def _data_dir() -> Path:
    return Path(__file__).parent.parent / "data"


def _load_strings() -> dict[str, Any]:
    if not _strings_cache:
        filepath = _data_dir() / "strings.yaml"
        if not filepath.exists():
            raise FileNotFoundError(f"Strings file not found: {filepath}")
        with open(filepath, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        violations = _collect_violations(data)
        if violations:
            raise ValueError(
                "data/strings.yaml contains strings that exceed Discord limits:\n"
                + "\n".join(violations)
            )
        _strings_cache.update(data)
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
