"""
engine/data_loader.py — Load and validate game data from JSON files.

Reads all files under data/actions/ and data/conditions/ at import time
and exposes two registries:

    ACTION_REGISTRY    : dict[str, ActionDef]
    CONDITION_REGISTRY : dict[str, ConditionDef]

Also reads data/classes/ and exposes:

    CLASS_DEFINITIONS  : dict[str, ClassDef]

These are consumed by tables.py (to build CharacterClass and CREATION_RULES)
and by engine/combat.py (to drive action dispatch and condition hooks).

Design notes
------------
- All registries are plain dicts keyed by the item's own ID/key field.
- Validation is strict at startup: a malformed data file raises ValueError
  immediately rather than producing a silent bad state at runtime.
- Callables are never stored in the data files.  Effect logic lives
  exclusively in engine/combat.py:_dispatch_hook().  Data files use
  string tags that the dispatcher maps to functions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate the data directory
# ---------------------------------------------------------------------------

# data/ sits alongside the project root, one level above engine/
_ENGINE_DIR  = Path(__file__).parent          # …/engine/
_PROJECT_DIR = _ENGINE_DIR.parent             # …/ (project root)
_DATA_DIR    = _PROJECT_DIR / "data"


# ---------------------------------------------------------------------------
# Data definition dataclasses
# (These describe the *definitions* loaded from disk, not runtime state.
#  Runtime state lives in models.py: ActiveCondition, CombatantState, etc.)
# ---------------------------------------------------------------------------

@dataclass
class ActionDef:
    """
    Definition of one combat action, loaded from data/actions/<id>.json.

    action_id          : Unique key, must match the filename stem.
    label              : Button label shown to the player.
    button_style       : Discord button style string: primary/secondary/danger/success.
    action_type        : Logical category: attack | move | affect.
    description        : Tooltip / help text.
    requires_target    : True → platform must collect a target_id before submitting.
    requires_destination: True → platform must collect a RangeBand destination.
    range_requirement  : List of RangeBand values (as strings) the attacker must
                         occupy to use this action.  Empty list = no restriction.
    effect_tags        : Ordered list of hook tags dispatched by _dispatch_hook().
    """
    action_id:            str        = ""
    label:                str        = ""
    button_style:         str        = "secondary"
    action_type:          str        = ""
    description:          str        = ""
    requires_target:      bool       = False
    requires_destination: bool       = False
    range_requirement:    list[str]  = field(default_factory=list)
    effect_tags:          list[str]  = field(default_factory=list)


@dataclass
class ConditionDef:
    """
    Definition of one status condition, loaded from data/conditions/<id>.json.

    condition_id  : Unique key, must match the filename stem.
    label         : Display name shown to players.
    duration_type : "rounds" | "permanent".
    hooks         : Dict mapping hook names to effect tag strings (or null).
                    Recognised hook names:
                      on_turn_start, on_turn_end, on_attack, on_hit,
                      on_take_damage, on_death, on_move
    stat_modifiers: Dict mapping ability name → integer modifier
                    (e.g. {"strength": -2}).  Applied for the condition's
                    duration.
    grants_actions: List of action IDs added to the combatant's available
                    actions while this condition is active.
    """
    condition_id:   str               = ""
    label:          str               = ""
    duration_type:  str               = "rounds"
    hooks:          dict[str, str | None] = field(default_factory=dict)
    stat_modifiers: dict[str, int]    = field(default_factory=dict)
    grants_actions: list[str]         = field(default_factory=list)


@dataclass
class ClassDef:
    """
    Definition of one player class, loaded from data/classes/<key>.json.

    key              : Enum key (e.g. "FIGHTER").  Must match filename stem
                       (case-insensitive) and be unique.
    display_name     : Player-facing name (e.g. "Magic-User").
    hit_die          : Hit die size (e.g. 8 for d8).
    base_ac          : Unarmoured AC (descending; lower = better).
    base_movement    : Movement speed in feet per turn.
    is_spellcaster   : Whether the class uses the spell system.
    default_saves    : Dict of saving throw targets (opaque to the engine).
    pack_bonus_items : Dict mapping pack name → (item_name, qty, encumbrance)
                       for the class-specific bonus item in that pack.
    combat_actions   : Ordered list of action IDs available to this class
                       by default in ROUNDS mode.  "affect" should always
                       be last.
    """
    key:              str               = ""
    display_name:     str               = ""
    hit_die:          int               = 6
    base_ac:          int               = 9
    base_movement:    int               = 120
    is_spellcaster:   bool              = False
    default_saves:    dict[str, int]    = field(default_factory=dict)
    pack_bonus_items: dict              = field(default_factory=dict)
    combat_actions:   list[str]         = field(default_factory=list)


# ---------------------------------------------------------------------------
# Required JSON keys for each file type
# ---------------------------------------------------------------------------

_ACTION_REQUIRED = {
    "action_id", "label", "button_style", "action_type",
    "requires_target", "requires_destination", "effect_tags",
}

_CONDITION_REQUIRED = {
    "condition_id", "label", "duration_type", "hooks",
}

_CLASS_REQUIRED = {
    "key", "display_name", "hit_die", "base_ac", "base_movement",
    "is_spellcaster", "default_saves", "combat_actions",
}

_VALID_BUTTON_STYLES  = {"primary", "secondary", "danger", "success"}
_VALID_ACTION_TYPES   = {"attack", "move", "affect"}
_VALID_DURATION_TYPES = {"rounds", "permanent"}
_VALID_HOOK_NAMES     = {
    "on_turn_start", "on_turn_end", "on_attack", "on_hit",
    "on_take_damage", "on_death", "on_move",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    """Read and parse one JSON file; raise ValueError with the path on failure."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def _validate_keys(data: dict, required: set[str], path: Path) -> None:
    missing = required - data.keys()
    if missing:
        raise ValueError(f"{path}: missing required keys: {sorted(missing)}")


def _load_action(path: Path) -> ActionDef:
    data = _load_json(path)
    _validate_keys(data, _ACTION_REQUIRED, path)

    action_id = data["action_id"]
    if action_id != path.stem:
        raise ValueError(
            f"{path}: 'action_id' value '{action_id}' must match filename stem '{path.stem}'"
        )

    style = data["button_style"]
    if style not in _VALID_BUTTON_STYLES:
        raise ValueError(f"{path}: invalid button_style '{style}'; must be one of {_VALID_BUTTON_STYLES}")

    atype = data["action_type"]
    if atype not in _VALID_ACTION_TYPES:
        raise ValueError(f"{path}: invalid action_type '{atype}'; must be one of {_VALID_ACTION_TYPES}")

    return ActionDef(
        action_id=action_id,
        label=data["label"],
        button_style=style,
        action_type=atype,
        description=data.get("description", ""),
        requires_target=bool(data["requires_target"]),
        requires_destination=bool(data["requires_destination"]),
        range_requirement=list(data.get("range_requirement", [])),
        effect_tags=list(data["effect_tags"]),
    )


def _load_condition(path: Path) -> ConditionDef:
    data = _load_json(path)
    _validate_keys(data, _CONDITION_REQUIRED, path)

    condition_id = data["condition_id"]
    if condition_id != path.stem:
        raise ValueError(
            f"{path}: 'condition_id' value '{condition_id}' must match filename stem '{path.stem}'"
        )

    dtype = data["duration_type"]
    if dtype not in _VALID_DURATION_TYPES:
        raise ValueError(
            f"{path}: invalid duration_type '{dtype}'; must be one of {_VALID_DURATION_TYPES}"
        )

    hooks = dict(data["hooks"])
    unknown_hooks = set(hooks.keys()) - _VALID_HOOK_NAMES
    if unknown_hooks:
        raise ValueError(f"{path}: unknown hook name(s): {sorted(unknown_hooks)}")

    return ConditionDef(
        condition_id=condition_id,
        label=data["label"],
        duration_type=dtype,
        hooks=hooks,
        stat_modifiers=dict(data.get("stat_modifiers", {})),
        grants_actions=list(data.get("grants_actions", [])),
    )


def _load_class(path: Path) -> ClassDef:
    data = _load_json(path)
    _validate_keys(data, _CLASS_REQUIRED, path)

    key = data["key"]
    if key.upper() != path.stem.upper():
        raise ValueError(
            f"{path}: 'key' value '{key}' must match filename stem '{path.stem}' (case-insensitive)"
        )

    # pack_bonus_items values are stored as [name, qty, enc] arrays in JSON;
    # convert to tuples to match the existing tables.py convention.
    raw_pack = data.get("pack_bonus_items", {})
    pack_bonus: dict = {}
    for pack_name, item in raw_pack.items():
        if isinstance(item, list) and len(item) == 3:
            pack_bonus[pack_name] = tuple(item)
        else:
            raise ValueError(
                f"{path}: pack_bonus_items['{pack_name}'] must be a 3-element array "
                f"[name, quantity, encumbrance], got {item!r}"
            )

    return ClassDef(
        key=key.upper(),
        display_name=data["display_name"],
        hit_die=int(data["hit_die"]),
        base_ac=int(data["base_ac"]),
        base_movement=int(data["base_movement"]),
        is_spellcaster=bool(data["is_spellcaster"]),
        default_saves=dict(data["default_saves"]),
        pack_bonus_items=pack_bonus,
        combat_actions=list(data["combat_actions"]),
    )


# ---------------------------------------------------------------------------
# Registry builders
# ---------------------------------------------------------------------------

def _build_action_registry(actions_dir: Path) -> dict[str, ActionDef]:
    registry: dict[str, ActionDef] = {}
    if not actions_dir.exists():
        return registry
    for path in sorted(actions_dir.glob("*.json")):
        defn = _load_action(path)
        if defn.action_id in registry:
            raise ValueError(f"Duplicate action_id '{defn.action_id}' (from {path})")
        registry[defn.action_id] = defn
    return registry


def _build_condition_registry(conditions_dir: Path) -> dict[str, ConditionDef]:
    registry: dict[str, ConditionDef] = {}
    if not conditions_dir.exists():
        return registry
    for path in sorted(conditions_dir.glob("*.json")):
        defn = _load_condition(path)
        if defn.condition_id in registry:
            raise ValueError(f"Duplicate condition_id '{defn.condition_id}' (from {path})")
        registry[defn.condition_id] = defn
    return registry


def _build_class_definitions(classes_dir: Path) -> dict[str, ClassDef]:
    definitions: dict[str, ClassDef] = {}
    if not classes_dir.exists():
        return definitions
    for path in sorted(classes_dir.glob("*.json")):
        defn = _load_class(path)
        if defn.key in definitions:
            raise ValueError(f"Duplicate class key '{defn.key}' (from {path})")
        definitions[defn.key] = defn
    return definitions


# ---------------------------------------------------------------------------
# Cross-registry validation
# ---------------------------------------------------------------------------

def _cross_validate(
    action_registry: dict[str, ActionDef],
    condition_registry: dict[str, ConditionDef],
    class_definitions: dict[str, ClassDef],
) -> None:
    """
    Ensure all references between registries are consistent.
    Called once after all files are loaded.
    """
    # Every action referenced in a class's combat_actions must exist
    for key, cls_def in class_definitions.items():
        for action_id in cls_def.combat_actions:
            if action_id not in action_registry:
                raise ValueError(
                    f"Class '{key}' references unknown action_id '{action_id}' "
                    f"in combat_actions. Add data/actions/{action_id}.json."
                )

    # Every action referenced in a condition's grants_actions must exist
    for cond_id, cond_def in condition_registry.items():
        for action_id in cond_def.grants_actions:
            if action_id not in action_registry:
                raise ValueError(
                    f"Condition '{cond_id}' references unknown action_id '{action_id}' "
                    f"in grants_actions. Add data/actions/{action_id}.json."
                )


# ---------------------------------------------------------------------------
# Public API — loaded once at import time
# ---------------------------------------------------------------------------

def load_all(data_dir: Path = _DATA_DIR) -> tuple[
    dict[str, ActionDef],
    dict[str, ConditionDef],
    dict[str, ClassDef],
]:
    """
    Load and validate all data files under data_dir.
    Returns (action_registry, condition_registry, class_definitions).
    Raises ValueError on any schema or cross-reference error.

    Exposed for testing with a custom data_dir; normal code uses the
    module-level constants below.
    """
    action_registry    = _build_action_registry(data_dir / "actions")
    condition_registry = _build_condition_registry(data_dir / "conditions")
    class_definitions  = _build_class_definitions(data_dir / "classes")
    _cross_validate(action_registry, condition_registry, class_definitions)
    return action_registry, condition_registry, class_definitions


# Load at import time.  Any data error raises immediately so the bot
# won't start with inconsistent game data.
ACTION_REGISTRY, CONDITION_REGISTRY, CLASS_DEFINITIONS = load_all()
