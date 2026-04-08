"""
engine/data_loader.py — Load and validate game data from JSON files.

Reads all files under data/actions/, data/conditions/, and data/classes/
at import time and exposes three registries:

    ACTION_REGISTRY    : dict[str, ActionDef]
    CONDITION_REGISTRY : dict[str, ConditionDef]
    CLASS_DEFINITIONS  : dict[str, JobDef]     (keyed by UPPERCASE job key)
    SKILL_REGISTRY     : dict[str, SkillDef]   (keyed by skill id, all jobs)

These are consumed by azure_engine.py (to build CharacterClass / Job enum and
CREATION_RULES) and by engine/combat.py (action dispatch, condition hooks,
skill lookups).

Hook format
-----------
A hook value in a condition or action effect_tags list can be either:

  • A plain string — the tag name, no parameters:
        "on_turn_start": "skip_action"
        "effect_tags": ["check_death"]

  • A hook object — tag name plus parameters dict:
        "on_turn_end": {"tag": "deal_damage", "dice": "1d4", "type": "poison"}
        "effect_tags": [{"tag": "melee_attack", "dice": "1d6"}]

Both forms are valid everywhere.  _dispatch_hook() in combat.py handles
unwrapping transparently.  See CONTRIBUTING.md for how to add new hooks.

Job / Skill files
-----------------
Each job lives in data/classes/<key>.json.  The key (filename stem,
uppercased) identifies the job throughout the engine.

SkillDef objects are loaded from the same per-job files when a
"skills" array is present, but the primary source of truth is the
companion data/classes/<key>_skills.json file (optional).  All skills
across all jobs are merged into SKILL_REGISTRY keyed by skill id.
Duplicate skill ids across jobs are allowed (e.g. shared skills like
"trapwise") — the last writer wins in SKILL_REGISTRY, but each JobDef
carries its own copy of the skills it grants.

Design notes
------------
- All registries are plain dicts; no callables are stored.
- Validation is strict at startup: a malformed data file raises ValueError
  immediately rather than producing a silent bad state at runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.azure_constants import SkillType
from engine.item import Item, createItemFromData

# ---------------------------------------------------------------------------
# Locate the data directory
# ---------------------------------------------------------------------------

_ENGINE_DIR  = Path(__file__).parent          # …/engine/
_PROJECT_DIR = _ENGINE_DIR.parent             # …/ (project root)
_DATA_DIR    = _PROJECT_DIR / "data"


# ---------------------------------------------------------------------------
# Type alias for a hook entry
# ---------------------------------------------------------------------------

# A hook value is either:
#   str  — plain tag name, no params  (e.g. "skip_action")
#   dict — {"tag": "...", ...params}  (e.g. {"tag": "deal_damage", "dice": "1d4"})
#   None — no effect
HookEntry = str | dict | None


# ---------------------------------------------------------------------------
# Data definition dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ActionDef:
    """
    Definition of one combat action, loaded from data/actions/<id>.json.

    action_id          : Unique key, must match the filename stem.
    label              : Button label shown to the player.
    button_style       : Discord button style: primary/secondary/danger/success.
    action_type        : Logical category: attack | move | affect.
    description        : Tooltip / help text.
    requires_target    : True → platform must collect a target_id before submitting.
    requires_destination: True → platform must collect a RangeBand destination.
    range_requirement  : Maximum band distance from actor to target, or "weapon" to use
                         the equipped weapon's range, or None for no restriction.
    effect_tags        : Ordered list of hook entries dispatched by _dispatch_hook().
                         Each entry is a plain tag string or a hook object dict.
    """
    action_id:            str              = ""
    label:                str              = ""
    button_style:         str              = "secondary"
    action_type:          str              = ""
    description:          str              = ""
    requires_target:      bool             = False
    requires_destination: bool             = False
    range_requirement:    int | str | None = None
    effect_tags:          list[HookEntry]  = field(default_factory=list)


@dataclass
class ConditionDef:
    """
    Definition of one status condition, loaded from data/conditions/<id>.json.

    condition_id  : Unique key, must match the filename stem.
    label         : Display name shown to players.
    duration_type : "rounds" | "permanent".
    hooks         : Dict mapping hook names to HookEntry values (str, dict, or null).
                    Recognised hook names:
                      on_turn_start — fires before the actor's action this round
                      on_turn_end   — fires at end of round (after all actions)
                      on_attack     — fires when the combatant makes an attack
                      on_hit        — fires when the combatant lands a hit
                      on_take_damage — fires when the combatant receives damage
                      on_death      — fires when the combatant reaches 0 HP
                      on_move       — fires inside move_to_band before movement
    stat_modifiers: Dict mapping stat name → integer modifier scaled by POWER_LEVEL
                    (e.g. {"physique": -100} = -1 effective Physique).
    grants_actions: List of action IDs added to the combatant's available
                    actions while this condition is active.
    """
    condition_id:   str                  = ""
    label:          str                  = ""
    duration_type:  str                  = "rounds"
    hooks:          dict[str, HookEntry] = field(default_factory=dict)
    stat_modifiers: dict[str, int]       = field(default_factory=dict)
    grants_actions: list[str]            = field(default_factory=list)
    stackable:      bool                 = False
    tags:           list[str]            = field(default_factory=list)


@dataclass
class SkillDef:
    """
    Definition of one job skill, loaded from data/jobskills/skills.json.

    Skills are permanent character benefits gained at level-up.  Some skills
    happen to grant access to a combat action button (type == COMBAT_ACTION),
    but the skill and the action are distinct concepts — the skill records
    what was learned, the action is what the button does at runtime.

    skill_id    : Unique string key (e.g. "knight_protector").
    name        : Display name (e.g. "Protector").
    source      : Job id this skill originates from (e.g. "knight").
    level       : Job level at which this skill is unlocked.
    skill_type  : Integer matching SkillType enum in azure_constants.py:
                    0 SIMPLE, 1 TURN_ACTION, 2 COMBAT_ACTION, 3 ORACLE_ACTION,
                    4 FREE_ACTION, 5 PASSIVE_BONUS, 6 WEAPON_RANK, 7 ROLEPLAY,
                    8 STATUS, 9 COMPLEX
    description : Player-facing description.
    dm_notes    : DM-only notes (not shown to players).
    action_id   : For COMBAT_ACTION/TURN_ACTION/ORACLE_ACTION/FREE_ACTION skills —
                  the action_id in ACTION_REGISTRY this skill grants access to.
    stat        : For PASSIVE_BONUS skills — the stat being boosted
                  ("PHY", "FNS", "RSN", "SVY", "ANY", "SAVE").
    bonus       : For PASSIVE_BONUS skills — bonus amount (raw, not scaled).
    rank        : For WEAPON_RANK skills — the weapon rank unlocked ("E"–"A",
                  or arcane ranks "V"–"Z").
    uses        : For skills with limited uses per encounter/day — count.
                  None means unlimited.
    check       : For skills requiring a check — {"DC": int, "Stat": str} or None.
    """
    skill_id:    str        = ""
    name:        str        = ""
    source:      str        = ""
    level:       int        = 1
    skill_type:  int        = 0
    description: str        = ""
    dm_notes:    str        = ""
    action_id:   str | None = None
    stat:        str | None = None
    bonus:       int        = 0
    rank:        str | None = None
    uses:        int | None = None
    check:       dict | None = None


@dataclass
class JobDef:
    """
    Definition of one job, loaded from data/classes/<key>.json.

    key          : Uppercase identifier matching the filename stem and
                   the CharacterClass enum member name (e.g. "KNIGHT").
    display_name : Player-facing name (e.g. "Knight").
    hit_die      : HP die size (e.g. 12 for d12), pre-scaled by POWER_LEVEL
                   at creation time.
    base_save    : Starting save value (raw, scaled by POWER_LEVEL at creation).
    primary_stat : Which of the four stats (PHY/FNS/RSN/SVY) grows on level-up.
    max_level    : Maximum level for this job.
    description  : Job flavour / lore text.
    skills       : All skills associated with this job, keyed by skill_id.
                   Loaded from data/jobskills/skills.json.
                   Equip rank access, combat actions, and passive bonuses are all
                   derived from skills rather than hardcoded fields.
    """
    key:            str             = ""
    display_name:   str             = ""
    hit_die:        int             = 6
    base_save:      int             = 0
    primary_stat:   str             = "PHY"
    max_level:      int             = 5
    description:    str             = ""
    skills:         dict[str, SkillDef] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Required JSON keys
# ---------------------------------------------------------------------------

_ACTION_REQUIRED = {
    "action_id", "label", "button_style", "action_type",
    "requires_target", "requires_destination", "effect_tags",
}
_CONDITION_REQUIRED = {
    "condition_id", "label", "duration_type", "hooks",
}
_JOB_REQUIRED = {
    "key", "display_name", "hit_die",
    "base_save", "primary_stat", "max_level",
}
_SKILL_DEF_REQUIRED = {"name", "type"}
_SKILL_GRANT_REQUIRED = {"id", "level"}

_VALID_BUTTON_STYLES  = {"primary", "secondary", "danger", "success"}
_VALID_ACTION_TYPES   = {"attack", "move", "affect", "combat"}
_VALID_DURATION_TYPES = {"rounds", "permanent"}
_VALID_HOOK_NAMES     = {
    "on_turn_start", "on_turn_end", "on_attack", "on_hit",
    "on_take_damage", "on_death", "on_move",
}
_VALID_PRIMARY_STATS  = {"PHY", "FNS", "RSN", "SVY"}
_VALID_WEAPON_RANKS   = {"E", "D", "C", "B", "A", "V", "W", "X", "Y", "Z"}


# ---------------------------------------------------------------------------
# Hook entry validators
# ---------------------------------------------------------------------------

def _validate_hook_entry(entry: HookEntry, path: Path, hook_name: str) -> None:
    if entry is None or isinstance(entry, str):
        return
    if isinstance(entry, dict):
        if "tag" not in entry:
            raise ValueError(
                f"{path}: hook '{hook_name}' object is missing required key 'tag'. "
                f"Hook objects must be {{\"tag\": \"tag_name\", ...params}}. Got: {entry!r}"
            )
        if not isinstance(entry["tag"], str):
            raise ValueError(
                f"{path}: hook '{hook_name}' object 'tag' must be a string. Got: {entry['tag']!r}"
            )
        return
    raise ValueError(
        f"{path}: hook '{hook_name}' value must be a string, object with 'tag' key, "
        f"or null. Got: {entry!r}"
    )


def _validate_effect_tag(entry: HookEntry, path: Path, idx: int) -> None:
    if isinstance(entry, str):
        return
    if isinstance(entry, dict):
        if "tag" not in entry:
            raise ValueError(
                f"{path}: effect_tags[{idx}] object is missing required key 'tag'. "
                f"Got: {entry!r}"
            )
        if not isinstance(entry["tag"], str):
            raise ValueError(
                f"{path}: effect_tags[{idx}] 'tag' must be a string. Got: {entry['tag']!r}"
            )
        return
    raise ValueError(
        f"{path}: effect_tags[{idx}] must be a string or object with 'tag' key. "
        f"Got: {entry!r}"
    )


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
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


# ---------------------------------------------------------------------------
# Action loader
# ---------------------------------------------------------------------------

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
        raise ValueError(
            f"{path}: invalid button_style '{style}'; must be one of {_VALID_BUTTON_STYLES}"
        )

    atype = data["action_type"]
    if atype not in _VALID_ACTION_TYPES:
        raise ValueError(
            f"{path}: invalid action_type '{atype}'; must be one of {_VALID_ACTION_TYPES}"
        )

    raw_tags = list(data["effect_tags"])
    for i, entry in enumerate(raw_tags):
        _validate_effect_tag(entry, path, i)

    return ActionDef(
        action_id=action_id,
        label=data["label"],
        button_style=style,
        action_type=atype,
        description=data.get("description", ""),
        requires_target=bool(data["requires_target"]),
        requires_destination=bool(data["requires_destination"]),
        range_requirement=data.get("range_requirement"),
        effect_tags=raw_tags,
    )


# ---------------------------------------------------------------------------
# Condition loader
# ---------------------------------------------------------------------------

def _load_condition(path: Path) -> ConditionDef:
    data = _load_json(path)
    _validate_keys(data, _CONDITION_REQUIRED, path)

    condition_id = data["condition_id"]
    if condition_id != path.stem:
        raise ValueError(
            f"{path}: 'condition_id' value '{condition_id}' must match "
            f"filename stem '{path.stem}'"
        )

    dtype = data["duration_type"]
    if dtype not in _VALID_DURATION_TYPES:
        raise ValueError(
            f"{path}: invalid duration_type '{dtype}'; "
            f"must be one of {_VALID_DURATION_TYPES}"
        )

    hooks: dict[str, HookEntry] = {}
    for hook_name, entry in data["hooks"].items():
        if hook_name not in _VALID_HOOK_NAMES:
            raise ValueError(
                f"{path}: unknown hook name '{hook_name}'; "
                f"valid names: {sorted(_VALID_HOOK_NAMES)}"
            )
        _validate_hook_entry(entry, path, hook_name)
        hooks[hook_name] = entry

    return ConditionDef(
        condition_id=condition_id,
        label=data["label"],
        duration_type=dtype,
        hooks=hooks,
        stat_modifiers=dict(data.get("stat_modifiers", {})),
        grants_actions=list(data.get("grants_actions", [])),
        stackable=bool(data.get("stackable", False)),
        tags=list(data.get("tags", [])),
    )


# ---------------------------------------------------------------------------
# Skill loader
# ---------------------------------------------------------------------------

def _load_skill_definition(skill_id: str, sdata: dict, path: Path) -> SkillDef:
    """
    Parse one skill definition from the "definitions" block of skills.json.
    `skill_id` is the key in the definitions dict (e.g. "advance").
    `sdata` is the value dict; it must NOT contain source or level
    (those are assigned per-job in the grants block).
    """
    _validate_keys(sdata, _SKILL_DEF_REQUIRED, path)

    skill_type = int(sdata["type"])
    uses_raw   = sdata.get("uses")

    skill = SkillDef(
        skill_id=skill_id,
        name=sdata["name"],
        source="",    # filled in when the skill is assigned to a job
        level=0,      # filled in from the grants block
        skill_type=skill_type,
        description=sdata.get("desc", ""),
        dm_notes=sdata.get("dm_notes", ""),
        action_id=sdata.get("action_id"),
        uses=int(uses_raw) if uses_raw is not None else None,
        check=sdata.get("check"),
    )

    # WEAPON_RANK
    if skill_type == SkillType.WEAPON_RANK.value:
        rank = sdata.get("rank", "E")
        if rank not in _VALID_WEAPON_RANKS:
            raise ValueError(
                f"{path}: skill '{skill_id}' has invalid weapon rank '{rank}'; "
                f"valid ranks: {sorted(_VALID_WEAPON_RANKS)}"
            )
        skill.rank = rank

    # PASSIVE_BONUS
    if skill_type == SkillType.PASSIVE_BONUS.value:
        skill.stat  = sdata.get("stat")
        skill.bonus = int(sdata.get("bonus", 1))

    return skill


def _load_skill_definitions(jobskills_dir: Path) -> dict[str, SkillDef]:
    """
    Load the "definitions" block from data/jobskills/skills.json.
    Returns a flat dict[skill_id, SkillDef] with source and level unset
    (those are filled in per-job when resolving grants in _load_job).
    """
    skills_path = jobskills_dir / "skills.json"
    if not skills_path.exists():
        return {}
    data = _load_json(skills_path)
    defs: dict[str, SkillDef] = {}
    for skill_id, sdata in data.get("definitions", {}).items():
        defs[skill_id] = _load_skill_definition(skill_id, sdata, skills_path)
    return defs


# ---------------------------------------------------------------------------
# Job loader
# ---------------------------------------------------------------------------

def _load_job(path: Path, skill_defs: dict[str, SkillDef]) -> JobDef:
    """
    Load one job from data/classes/<key>.json.
    The job's "skills" array (list of {id, level} grants) is resolved
    against skill_defs to build JobDef.skills.
    """
    import dataclasses

    data = _load_json(path)
    _validate_keys(data, _JOB_REQUIRED, path)

    key = data["key"]
    if key.upper() != path.stem.upper():
        raise ValueError(
            f"{path}: 'key' value '{key}' must match filename stem "
            f"'{path.stem}' (case-insensitive)"
        )

    primary_stat = data["primary_stat"]
    if primary_stat not in _VALID_PRIMARY_STATS:
        raise ValueError(
            f"{path}: invalid primary_stat '{primary_stat}'; "
            f"must be one of {_VALID_PRIMARY_STATS}"
        )

    job_key_lower = key.lower()
    skills: dict[str, SkillDef] = {}
    for grant in data.get("skills", []):
        _validate_keys(grant, _SKILL_GRANT_REQUIRED, path)
        skill_id = grant["id"]
        level    = int(grant["level"])
        base = skill_defs.get(skill_id)
        if base is None:
            raise ValueError(
                f"{path}: job '{job_key_lower}' grants unknown skill id "
                f"'{skill_id}'. Add it to data/jobskills/skills.json."
            )
        skills[skill_id] = dataclasses.replace(base, source=job_key_lower, level=level)

    return JobDef(
        key=key.upper(),
        display_name=data["display_name"],
        hit_die=int(data["hit_die"]),
        base_save=int(data["base_save"]),
        primary_stat=primary_stat,
        max_level=int(data["max_level"]),
        description=data.get("description", ""),
        skills=skills,
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
            raise ValueError(
                f"Duplicate condition_id '{defn.condition_id}' (from {path})"
            )
        registry[defn.condition_id] = defn
    return registry


def _build_job_definitions(
    classes_dir: Path,
    jobskills_dir: Path,
) -> tuple[dict[str, JobDef], dict[str, SkillDef]]:
    """
    Load all job files from classes_dir and skills from jobskills_dir/skills.json.
    Returns (job_definitions, skill_registry).
    skill_registry is a flat dict of all skills across all jobs.
    Shared skills (same id, multiple jobs) are stored once — last writer wins
    in the flat registry, but each JobDef keeps its own copy.
    """
    skill_defs = _load_skill_definitions(jobskills_dir)
    job_defs:      dict[str, JobDef]  = {}
    skill_registry: dict[str, SkillDef] = {}
    if not classes_dir.exists():
        return job_defs, skill_registry
    for path in sorted(classes_dir.glob("*.json")):
        defn = _load_job(path, skill_defs)
        if defn.key in job_defs:
            raise ValueError(f"Duplicate job key '{defn.key}' (from {path})")
        job_defs[defn.key] = defn
        skill_registry.update(defn.skills)
    return job_defs, skill_registry

def _build_item_registry(items_dir: Path) -> dict[str, Item]:
    """
    Load all items from data/items/items.json.
    The JSON is expected to be in the normalised native format produced by
    scripts/google_sheets_sync.py — no translation is needed here.
    Returns a flat dict of all items keyed by item_id.
    """
    registry: dict[str, Item] = {}
    items_file = items_dir / "items.json"
    if not items_file.exists():
        return registry

    try:
        with open(items_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {items_file}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read {items_file}: {exc}") from exc

    for _category, items_list in data.items():
        for item_data in items_list:
            item = createItemFromData(item_data)
            if item is not None:
                if item.item_id in registry:
                    raise ValueError(f"Duplicate item_id '{item.item_id}' in {items_file}")
                registry[item.item_id] = item
    return registry


# ---------------------------------------------------------------------------
# Cross-registry validation
# ---------------------------------------------------------------------------

def _cross_validate(
    action_registry:    dict[str, ActionDef],
    condition_registry: dict[str, ConditionDef],
    class_definitions:  dict[str, JobDef],
) -> None:
    """Ensure all cross-references between registries are consistent."""
    _ACTION_SKILL_TYPES = {
        SkillType.TURN_ACTION.value,
        SkillType.COMBAT_ACTION.value,
        SkillType.ORACLE_ACTION.value,
        SkillType.FREE_ACTION.value,
    }
    for key, job_def in class_definitions.items():
        for skill in job_def.skills.values():
            if skill.skill_type in _ACTION_SKILL_TYPES and skill.action_id is not None and skill.action_id not in action_registry:
                raise ValueError(
                    f"Job '{key}' skill '{skill.name}' references unknown "
                    f"action_id '{skill.action_id}'. "
                    f"Add data/actions/{skill.action_id}.json."
                )

    for cond_id, cond_def in condition_registry.items():
        for action_id in cond_def.grants_actions:
            if action_id not in action_registry:
                raise ValueError(
                    f"Condition '{cond_id}' references unknown action_id "
                    f"'{action_id}' in grants_actions. "
                    f"Add data/actions/{action_id}.json."
                )


# ---------------------------------------------------------------------------
# Public API — loaded once at import time
# ---------------------------------------------------------------------------

def load_all(data_dir: Path = _DATA_DIR) -> tuple[
    dict[str, ActionDef],
    dict[str, ConditionDef],
    dict[str, JobDef],
    dict[str, SkillDef],
    dict[str, Item],
]:
    """
    Load and validate all data files under data_dir.
    Returns (action_registry, condition_registry, class_definitions, skill_registry).
    Raises ValueError on any schema or cross-reference error.

    Exposed for testing with a custom data_dir; normal code uses the
    module-level constants below.
    """
    action_registry    = _build_action_registry(data_dir / "actions")
    condition_registry = _build_condition_registry(data_dir / "conditions")
    class_definitions, skill_registry = _build_job_definitions(
        data_dir / "classes", data_dir / "jobskills"
    )
    _cross_validate(action_registry, condition_registry, class_definitions)
    item_registry = _build_item_registry(data_dir / "items")
    return action_registry, condition_registry, class_definitions, skill_registry, item_registry


# Load at import time — any data error raises immediately so the bot
# won't start with inconsistent game data.
ACTION_REGISTRY, CONDITION_REGISTRY, CLASS_DEFINITIONS, SKILL_REGISTRY, ITEM_REGISTRY = load_all()
