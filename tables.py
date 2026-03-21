"""
Static game reference tables and creation rules for the Azure ruleset.
All data is read-only.  No game state, no I/O.

Ruleset: Azure (custom OSR/JRPG hybrid)

Jobs (character classes) are loaded from data/classes/*.json.
The CharacterClass enum is generated automatically from those files,
so adding or renaming a job only requires editing the JSON.

Power scaling
-------------
Stats and HP are stored as large integers scaled by POWER_LEVEL (100).
A "base stat of 2" is stored as 200 internally.  This allows fractional
effects (e.g. a +0.5 stat from a passive bonus = +50 internally) and fast
integer arithmetic throughout the engine.  Divide by POWER_LEVEL to get
the human-readable value.

To adapt to a different ruleset:
  1. Edit data/classes/*.json to add/remove/rename jobs.
  2. Adjust POWER_LEVEL, LEVEL_MULTIPLIER, and BASE_INVENTORY_SIZE if needed.
  3. The CharacterClass enum and CREATION_RULES update automatically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Scaling constants
# ---------------------------------------------------------------------------

POWER_LEVEL       = 100   # all stats and HP stored as int × POWER_LEVEL
LEVEL_MULTIPLIER  = 2     # raw JSON level values are multiplied by this
BASE_INVENTORY_SIZE = 6   # base inventory slots before Physique modifier
MAX_LEVEL         = 99    # absolute character level ceiling


# ---------------------------------------------------------------------------
# Stat and skill enumerations
# ---------------------------------------------------------------------------

class Stat(Enum):
    """The four core stats of the Azure ruleset."""
    PHYSIQUE = "PHY"
    FINESSE  = "FNS"
    REASON   = "RSN"
    SAVVY    = "SVY"


class StatPriority(Enum):
    """
    Determines the die used when rolling stat growth on level-up.
    Die size = priority.value * POWER_LEVEL.
    NONE means the stat never grows from this job.
    """
    NONE     = 0
    LEAST    = 5
    LESSER   = 10
    AVERAGE  = 15
    GREATER  = 20
    GREATEST = 25


class SkillType(Enum):
    """
    Categories of skills gained from job levels.

    COMBAT_ACTION skills may grant access to a new action button, but
    the skill and the button are distinct — the skill records what was
    learned, the ACTION_REGISTRY records what the button does.
    """
    SIMPLE         = 0
    TURN_ACTION    = 1   # usable during an exploration turn
    COMBAT_ACTION  = 2   # may grant a new combat action button
    ORACLE_ACTION  = 3   # usable as an oracle (quick) action
    FREE_ACTION    = 4   # usable at any time without consuming an action
    PASSIVE_BONUS  = 5   # permanent stat bonus; no active use
    WEAPON_RANK    = 6   # unlocks a new weapon/gear rank
    ROLEPLAY       = 7   # social / narrative benefit
    STATUS         = 8   # a persistent character state
    COMPLEX        = 9   # multi-part or conditional ability


class Slot(Enum):
    """Equipment slots on a character."""
    MAIN      = "main"       # primary weapon
    OFF       = "off"        # off-hand weapon or shield
    HEAD      = "head"
    BODY      = "body"
    ARMS      = "arms"
    LEGS      = "legs"
    ACCESSORY = "accessory"  # multiple accessories may be allowed


# ---------------------------------------------------------------------------
# Weapon rank helpers
# ---------------------------------------------------------------------------

# All ranks in order, lowest to highest.
# Physical ranks: E D C B A
# Arcane ranks:   V W X Y Z  (for magic staves, tomes, etc.)
_PHYSICAL_RANKS = ("E", "D", "C", "B", "A")
_ARCANE_RANKS   = ("V", "W", "X", "Y", "Z")

def get_lower_weapon_ranks(rank: str) -> set[str]:
    """
    Return the set of all ranks at or below `rank` in the same tier.
    A character with rank "C" can equip E, D, and C gear.
    """
    for tier in (_PHYSICAL_RANKS, _ARCANE_RANKS):
        if rank in tier:
            idx = tier.index(rank)
            return set(tier[:idx + 1])
    return {"E"}


# ---------------------------------------------------------------------------
# Job (character class) definitions — loaded from data/classes/*.json
# ---------------------------------------------------------------------------

@dataclass
class PlayerClass:
    """
    Everything create_character needs to know about a job.
    Populated from data/classes/*.json via engine/data_loader.py.

    Renamed from PlayerClass for backward compatibility with engine/character.py.
    The underlying concept is a Job in Azure terminology.
    """
    display_name:   str             = "Unknown Job"
    hit_die:        int             = 6       # HP die size (pre-scale value)
    weapon_rank:    str             = "E"     # highest rank this job starts with
    base_save:      int             = 0       # starting save (pre-scale value)
    primary_stat:   str             = "PHY"   # "PHY" | "FNS" | "RSN" | "SVY"
    max_level:      int             = 5       # job level cap (pre-multiply value)
    description:    str             = ""
    combat_actions: list[str]       = field(default_factory=list)
    # stat_priority derived from primary_stat — GREATEST for primary, NONE otherwise
    stat_priority:  dict            = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.stat_priority:
            self.stat_priority = {
                Stat.PHYSIQUE: StatPriority.GREATEST if self.primary_stat == "PHY" else StatPriority.NONE,
                Stat.FINESSE:  StatPriority.GREATEST if self.primary_stat == "FNS" else StatPriority.NONE,
                Stat.REASON:   StatPriority.GREATEST if self.primary_stat == "RSN" else StatPriority.NONE,
                Stat.SAVVY:    StatPriority.GREATEST if self.primary_stat == "SVY" else StatPriority.NONE,
            }


def _build_class_definitions() -> dict[str, PlayerClass]:
    """
    Load JobDef entries from engine/data_loader and convert them to
    PlayerClass instances for the rest of the codebase.
    Uses the importlib path trick to avoid the circular import:
        tables → engine/__init__ → models → tables
    """
    import importlib.util
    import pathlib
    import sys

    _mod_name = "engine.data_loader"
    if _mod_name in sys.modules:
        _mod = sys.modules[_mod_name]
    else:
        _loader_path = pathlib.Path(__file__).parent / "engine" / "data_loader.py"
        _spec = importlib.util.spec_from_file_location(_mod_name, _loader_path)
        _mod  = importlib.util.module_from_spec(_spec)
        sys.modules[_mod_name] = _mod
        _spec.loader.exec_module(_mod)

    result: dict[str, PlayerClass] = {}
    for key, job_def in _mod.CLASS_DEFINITIONS.items():
        result[key] = PlayerClass(
            display_name=job_def.display_name,
            hit_die=job_def.hit_die,
            weapon_rank=job_def.weapon_rank,
            base_save=job_def.base_save,
            primary_stat=job_def.primary_stat,
            max_level=job_def.max_level,
            description=job_def.description,
            combat_actions=list(job_def.combat_actions),
        )
    return result


_CLASS_DEFINITIONS: dict[str, PlayerClass] = _build_class_definitions()

# CharacterClass enum: member name = uppercase key, value = display name
# e.g. CharacterClass.KNIGHT.value == "Knight"
CharacterClass = Enum(
    "CharacterClass",
    {key: rules.display_name for key, rules in _CLASS_DEFINITIONS.items()},
)

# Lookup: CharacterClass member → PlayerClass
CREATION_RULES: dict = {
    cls: _CLASS_DEFINITIONS[cls.name] for cls in CharacterClass
}


# ---------------------------------------------------------------------------
# Stat modifier helper (replaces B/X ABILITY_MODIFIERS lookup table)
# ---------------------------------------------------------------------------

def get_stat_modifier(stat_value: int) -> int:
    """
    Convert a scaled stat value to its effective modifier.

    Stats are stored as integers × POWER_LEVEL (100).
    The modifier is stat_value // POWER_LEVEL, rounded toward zero.
    Examples:
        200  →  2   (stat of 2 → +2 modifier)
        150  →  1   (stat of 1.5 → +1 modifier, floor)
       -100  → -1
          0  →  0

    This replaces the old B/X ABILITY_MODIFIERS lookup table.
    """
    if stat_value >= 0:
        return math.floor(stat_value / POWER_LEVEL)
    return math.ceil(stat_value / POWER_LEVEL)


# Kept for backward compatibility with any code that still imports these names.
# Will be removed after Stream B (models.py) refactor is complete.
ABILITY_MODIFIERS: dict = {}   # no longer a lookup table — use get_stat_modifier()
CON_HP_MODIFIER = ABILITY_MODIFIERS


# ---------------------------------------------------------------------------
# Inventory size helper
# ---------------------------------------------------------------------------

def get_inventory_size(physique: int) -> int:
    """
    Calculate inventory slot count from a character's scaled Physique stat.
    Base slots + floor(physique / POWER_LEVEL), minimum 1.
    """
    bonus = math.floor(physique / POWER_LEVEL)
    return max(1, BASE_INVENTORY_SIZE + bonus)


# ---------------------------------------------------------------------------
# Level-up helpers
# ---------------------------------------------------------------------------

def get_stat_growth_die(job: PlayerClass, stat: Stat) -> int:
    """
    Return the die size (pre-POWER_LEVEL scaling) for stat growth on level-up
    for the given job and stat.

    The rolled value should be divided by POWER_LEVEL to get the actual
    stat increase (preserving fractional growth over many levels).

    Die size = StatPriority.value * POWER_LEVEL.
    A NONE priority means this stat never grows from this job.
    """
    priority = job.stat_priority.get(stat, StatPriority.NONE)
    return priority.value * POWER_LEVEL


def get_effective_max_level(job: PlayerClass) -> int:
    """Return the true maximum level for a job after applying LEVEL_MULTIPLIER."""
    return job.max_level * LEVEL_MULTIPLIER


# ---------------------------------------------------------------------------
# Removed B/X tables — kept as empty stubs during transition
# to avoid import errors in files that haven't been updated yet.
# These will be deleted after Stream B and C are complete.
# ---------------------------------------------------------------------------

SAVING_THROWS: dict        = {}
SPELL_SLOTS_BY_CLASS: dict = {}
EQUIPMENT_PACKAGES: dict   = {}
EQUIPMENT_PACKAGE_DESCRIPTIONS: dict = {}
PACK_BONUS_DEFAULT: dict   = {}


def get_saving_throws(cls, level: int) -> dict:
    """
    Stub: saving throws are now a single integer (base_save) per job,
    not a five-category dict.  Returns an empty dict during transition.
    Use CREATION_RULES[cls].base_save * POWER_LEVEL for the character's save.
    """
    return {}


def get_spell_slots(cls, level: int) -> list:
    """
    Stub: spell slots are not yet implemented in the Azure ruleset.
    Returns an empty list during transition.
    """
    return []
