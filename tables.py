"""
Static game reference tables and creation rules.
All data is read-only. No game state, no I/O.

Ruleset: B/X D&D (Moldvay/Cook, 1981)

To adapt to a different ruleset:
  1. Edit data/classes/*.json to add/remove/rename classes
  2. Replace ability modifiers, saving throw tables, spell slots as needed
  3. Replace EQUIPMENT_PACKAGES
  The CharacterClass enum is generated from the JSON class files automatically,
  so models.py, engine.py, and all platform code require no changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Character creation rules
# ---------------------------------------------------------------------------

@dataclass
class PlayerClass:
    """
    Everything create_character needs to know about a class.
    Populated from data/classes/*.json via engine/data_loader.py.
    """

    display_name:     str   = "Unknown Class"   # shown to players, e.g. "Magic-User"
    hit_die:          int   = 6                 # die size for HP rolls
    base_ac:          int   = 9                 # unarmored AC (descending; lower = better)
    base_movement:    int   = 120               # feet per turn
    is_spellcaster:   bool  = False
    weapon_rank:      str   = "E"
    max_level:        int   = 5
    base_save:        int  = 0
    stat:             str  = "PHY"
    abilities=        dict = {}
    """
    id: str
    max_level: int = 5
    display_name: str = "Unknown Class"
    hit_die: int = 4
    weapon_rank: str='E'
    base_save: int = 0
    stat: str = "PHY"
    abilities={}
    """

    # Saving throw targets as an opaque dict.
    # Keys can be anything the ruleset uses; engine never inspects them.
    default_saves: dict = field(default_factory=lambda: {
        "death_poison": 14, "wands": 15, "paralysis_stone": 16,
        "breath_weapon": 17, "spells": 18,
    })

    # Class-specific bonus items for named equipment packs.
    # Maps pack_name -> (item_name, quantity, encumbrance).
    pack_bonus_items: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Class definitions — loaded from data/classes/*.json
# Edit those files to add, remove, or rename classes.
# The CharacterClass enum is generated from the loaded keys automatically.
# ---------------------------------------------------------------------------

def _build_class_definitions() -> dict[str, PlayerClass]:
    """
    Load ClassDef entries from engine/data_loader and convert them to
    PlayerClass instances that the rest of the codebase already knows.
    Raises ValueError (from data_loader) if any class file is malformed.
    """
    # Import the module directly by path to avoid the circular chain:
    #   tables → engine/__init__ → models → tables
    # engine/data_loader has no dependency on tables or models, so this is safe.
    # We must register the module in sys.modules before exec_module so that
    # Python 3.12's dataclass machinery can resolve the module's __dict__.
    import importlib.util
    import pathlib
    import sys

    _mod_name = "engine.data_loader"
    if _mod_name in sys.modules:
        # Already loaded (e.g. second import of tables) — reuse it.
        _mod = sys.modules[_mod_name]
    else:
        _loader_path = pathlib.Path(__file__).parent / "engine" / "data_loader.py"
        _spec = importlib.util.spec_from_file_location(_mod_name, _loader_path)
        _mod  = importlib.util.module_from_spec(_spec)
        sys.modules[_mod_name] = _mod   # register BEFORE exec so dataclasses can find it
        _spec.loader.exec_module(_mod)

    _json_defs = _mod.CLASS_DEFINITIONS
    result: dict[str, PlayerClass] = {}
    for key, cls_def in _json_defs.items():
        result[key] = PlayerClass(
            display_name=cls_def.display_name,
            hit_die=cls_def.hit_die,
            base_ac=cls_def.base_ac,
            base_movement=cls_def.base_movement,
            is_spellcaster=cls_def.is_spellcaster,
            default_saves=dict(cls_def.default_saves),
            pack_bonus_items=dict(cls_def.pack_bonus_items),
        )
    return result


_CLASS_DEFINITIONS: dict[str, PlayerClass] = _build_class_definitions()

# Generate CharacterClass enum from the loaded keys.
# cls.value == display_name (e.g. CharacterClass.FIGHTER.value == "Fighter")
CharacterClass = Enum(
    "CharacterClass",
    {key: rules.display_name for key, rules in _CLASS_DEFINITIONS.items()},
)

# Lookup: CharacterClass member -> PlayerClass
CREATION_RULES: dict = {
    cls: _CLASS_DEFINITIONS[cls.name] for cls in CharacterClass
}


# ---------------------------------------------------------------------------
# Ability score modifiers (B/X)
# ---------------------------------------------------------------------------

ABILITY_MODIFIERS: dict = {
    3: -3, 4: -2, 5: -2, 6: -1, 7: -1, 8: -1,
    9:  0, 10:  0, 11:  0, 12:  0,
    13:  1, 14:  1, 15:  1, 16:  2, 17:  2, 18:  3,
}
CON_HP_MODIFIER = ABILITY_MODIFIERS


# ---------------------------------------------------------------------------
# Saving throws by class and level
# ---------------------------------------------------------------------------

_ST = ["death_poison", "wands", "paralysis_stone", "breath_weapon", "spells"]

def _saves(*values):
    return dict(zip(_ST, values, strict=True))

SAVING_THROWS: dict = {
    (CharacterClass.FIGHTER, 1):  _saves(12, 13, 14, 15, 16),
    (CharacterClass.FIGHTER, 2):  _saves(12, 13, 14, 15, 16),
    (CharacterClass.FIGHTER, 3):  _saves(12, 13, 14, 15, 16),
    (CharacterClass.FIGHTER, 4):  _saves(10, 11, 12, 13, 14),
    (CharacterClass.FIGHTER, 5):  _saves(10, 11, 12, 13, 14),
    (CharacterClass.FIGHTER, 6):  _saves(10, 11, 12, 13, 14),
    (CharacterClass.FIGHTER, 7):  _saves( 8,  9, 10, 10, 12),
    (CharacterClass.FIGHTER, 8):  _saves( 8,  9, 10, 10, 12),
    (CharacterClass.FIGHTER, 9):  _saves( 8,  9, 10, 10, 12),
    (CharacterClass.FIGHTER, 10): _saves( 6,  7,  8,  8, 10),
    (CharacterClass.FIGHTER, 11): _saves( 6,  7,  8,  8, 10),
    (CharacterClass.FIGHTER, 12): _saves( 6,  7,  8,  8, 10),
    (CharacterClass.FIGHTER, 13): _saves( 4,  5,  6,  5,  8),
    (CharacterClass.FIGHTER, 14): _saves( 4,  5,  6,  5,  8),
    (CharacterClass.CLERIC, 1):   _saves(11, 12, 14, 16, 15),
    (CharacterClass.CLERIC, 2):   _saves(11, 12, 14, 16, 15),
    (CharacterClass.CLERIC, 3):   _saves(11, 12, 14, 16, 15),
    (CharacterClass.CLERIC, 4):   _saves( 9, 10, 12, 14, 12),
    (CharacterClass.CLERIC, 5):   _saves( 9, 10, 12, 14, 12),
    (CharacterClass.CLERIC, 6):   _saves( 9, 10, 12, 14, 12),
    (CharacterClass.CLERIC, 7):   _saves( 7,  8, 10, 12, 10),
    (CharacterClass.CLERIC, 8):   _saves( 7,  8, 10, 12, 10),
    (CharacterClass.CLERIC, 9):   _saves( 7,  8, 10, 12, 10),
    (CharacterClass.CLERIC, 10):  _saves( 5,  6,  8, 10,  8),
    (CharacterClass.CLERIC, 11):  _saves( 5,  6,  8, 10,  8),
    (CharacterClass.CLERIC, 12):  _saves( 5,  6,  8, 10,  8),
    (CharacterClass.CLERIC, 13):  _saves( 3,  4,  6,  8,  6),
    (CharacterClass.CLERIC, 14):  _saves( 3,  4,  6,  8,  6),
    (CharacterClass.MAGIC_USER, 1):  _saves(13, 14, 13, 16, 15),
    (CharacterClass.MAGIC_USER, 2):  _saves(13, 14, 13, 16, 15),
    (CharacterClass.MAGIC_USER, 3):  _saves(13, 14, 13, 16, 15),
    (CharacterClass.MAGIC_USER, 4):  _saves(11, 12, 11, 14, 12),
    (CharacterClass.MAGIC_USER, 5):  _saves(11, 12, 11, 14, 12),
    (CharacterClass.MAGIC_USER, 6):  _saves(11, 12, 11, 14, 12),
    (CharacterClass.MAGIC_USER, 7):  _saves( 9, 10,  9, 12,  9),
    (CharacterClass.MAGIC_USER, 8):  _saves( 9, 10,  9, 12,  9),
    (CharacterClass.MAGIC_USER, 9):  _saves( 9, 10,  9, 12,  9),
    (CharacterClass.MAGIC_USER, 10): _saves( 7,  8,  7, 10,  7),
    (CharacterClass.MAGIC_USER, 11): _saves( 7,  8,  7, 10,  7),
    (CharacterClass.MAGIC_USER, 12): _saves( 7,  8,  7, 10,  7),
    (CharacterClass.MAGIC_USER, 13): _saves( 5,  6,  5,  8,  5),
    (CharacterClass.MAGIC_USER, 14): _saves( 5,  6,  5,  8,  5),
    (CharacterClass.THIEF, 1):  _saves(13, 14, 13, 16, 15),
    (CharacterClass.THIEF, 2):  _saves(13, 14, 13, 16, 15),
    (CharacterClass.THIEF, 3):  _saves(13, 14, 13, 16, 15),
    (CharacterClass.THIEF, 4):  _saves(12, 13, 11, 14, 13),
    (CharacterClass.THIEF, 5):  _saves(12, 13, 11, 14, 13),
    (CharacterClass.THIEF, 6):  _saves(12, 13, 11, 14, 13),
    (CharacterClass.THIEF, 7):  _saves(10, 11,  9, 12, 11),
    (CharacterClass.THIEF, 8):  _saves(10, 11,  9, 12, 11),
    (CharacterClass.THIEF, 9):  _saves(10, 11,  9, 12, 11),
    (CharacterClass.THIEF, 10): _saves( 8,  9,  7, 10,  9),
    (CharacterClass.THIEF, 11): _saves( 8,  9,  7, 10,  9),
    (CharacterClass.THIEF, 12): _saves( 8,  9,  7, 10,  9),
    (CharacterClass.THIEF, 13): _saves( 6,  7,  5,  8,  7),
    (CharacterClass.THIEF, 14): _saves( 6,  7,  5,  8,  7),
    (CharacterClass.ELF, 1):  _saves(12, 13, 13, 15, 15),
    (CharacterClass.ELF, 2):  _saves(12, 13, 13, 15, 15),
    (CharacterClass.ELF, 3):  _saves(12, 13, 13, 15, 15),
    (CharacterClass.ELF, 4):  _saves(10, 11, 11, 13, 12),
    (CharacterClass.ELF, 5):  _saves(10, 11, 11, 13, 12),
    (CharacterClass.ELF, 6):  _saves(10, 11, 11, 13, 12),
    (CharacterClass.ELF, 7):  _saves( 8,  9,  9, 11,  9),
    (CharacterClass.ELF, 8):  _saves( 8,  9,  9, 11,  9),
    (CharacterClass.ELF, 9):  _saves( 8,  9,  9, 11,  9),
    (CharacterClass.ELF, 10): _saves( 6,  7,  7,  9,  7),
    (CharacterClass.HALFLING, 1): _saves(10, 13, 12, 13, 15),
    (CharacterClass.HALFLING, 2): _saves(10, 13, 12, 13, 15),
    (CharacterClass.HALFLING, 3): _saves(10, 13, 12, 13, 15),
    (CharacterClass.HALFLING, 4): _saves( 8, 11, 10, 10, 13),
    (CharacterClass.HALFLING, 5): _saves( 8, 11, 10, 10, 13),
    (CharacterClass.HALFLING, 6): _saves( 8, 11, 10, 10, 13),
    (CharacterClass.HALFLING, 7): _saves( 6,  9,  8,  7, 10),
    (CharacterClass.HALFLING, 8): _saves( 6,  9,  8,  7, 10),
}
for _lvl in range(1, 15):
    SAVING_THROWS[(CharacterClass.DWARF, _lvl)] = SAVING_THROWS[(CharacterClass.FIGHTER, _lvl)]


# ---------------------------------------------------------------------------
# Spell slot progression
# ---------------------------------------------------------------------------

MU_SPELL_SLOTS: dict = {
    1:  [1,0,0,0,0,0], 2:  [2,0,0,0,0,0], 3:  [2,1,0,0,0,0],
    4:  [2,2,0,0,0,0], 5:  [2,2,1,0,0,0], 6:  [2,2,2,0,0,0],
    7:  [3,2,2,1,0,0], 8:  [3,3,2,2,0,0], 9:  [3,3,3,2,1,0],
    10: [3,3,3,3,2,0], 11: [4,3,3,3,2,1], 12: [4,4,3,3,3,2],
    13: [4,4,4,3,3,3], 14: [4,4,4,4,3,3],
}
CLERIC_SPELL_SLOTS: dict = {
    1:  [0,0,0,0,0,0], 2:  [1,0,0,0,0,0], 3:  [2,0,0,0,0,0],
    4:  [2,1,0,0,0,0], 5:  [2,2,0,0,0,0], 6:  [2,2,1,1,0,0],
    7:  [2,2,2,1,1,0], 8:  [3,3,2,2,1,0], 9:  [3,3,3,2,2,0],
    10: [4,4,3,3,2,0], 11: [4,4,4,3,3,0], 12: [4,4,4,4,4,0],
    13: [5,5,4,4,4,0], 14: [5,5,5,4,4,0],
}
ELF_SPELL_SLOTS: dict = {
    1:  [1,0,0,0,0,0], 2:  [2,0,0,0,0,0], 3:  [2,1,0,0,0,0],
    4:  [2,2,0,0,0,0], 5:  [2,2,1,0,0,0], 6:  [2,2,2,0,0,0],
    7:  [3,2,2,1,0,0], 8:  [3,3,2,2,0,0], 9:  [3,3,3,2,1,0],
    10: [3,3,3,3,2,0],
}
SPELL_SLOTS_BY_CLASS: dict = {
    CharacterClass.MAGIC_USER: MU_SPELL_SLOTS,
    CharacterClass.CLERIC:     CLERIC_SPELL_SLOTS,
    CharacterClass.ELF:        ELF_SPELL_SLOTS,
}


# ---------------------------------------------------------------------------
# Equipment packages (B4 The Lost City)
# Class-specific pack bonus items live in CharacterCreationRules above.
# ---------------------------------------------------------------------------

EQUIPMENT_PACKAGES: dict = {
    "Pack A": [
        ("Backpack",        1, 0.0), ("Large Sack",      2, 0.2),
        ("Lantern",         1, 3.0), ("Oil Flask",       4, 0.5),
        ("Tinderbox",       1, 0.1), ("Iron Spikes",    12, 0.1),
        ("Hammer (small)",  1, 0.5), ("Waterskin",       1, 0.5),
        ("Rations (normal)",7, 0.5),
    ],
    "Pack B": [
        ("Backpack",        1, 0.0), ("Large Sack",      2, 0.2),
        ("Torch",           6, 0.1), ("Oil Flask",       3, 0.5),
        ("Tinderbox",       1, 0.1), ("10ft Pole",       1, 1.0),
        ("Rope 50ft",       1, 1.5), ("Waterskin",       1, 0.5),
        ("Rations (normal)",7, 0.5), ("Mirror (silver)", 1, 0.1),
    ],
    "Pack C": [
        ("Backpack",        1, 0.0), ("Small Sack",      4, 0.1),
        ("Iron Spikes",    12, 0.1), ("Rope 50ft",       1, 1.5),
        ("Waterskin",       1, 0.5), ("Rations (normal)",7, 0.5),
    ],
}

EQUIPMENT_PACKAGE_DESCRIPTIONS: dict = {
    "Pack A": (
        "Backpack, 2 large sacks, lantern, 4 oil flasks, tinderbox, "
        "12 iron spikes, small hammer, waterskin, 7 days rations"
    ),
    "Pack B": (
        "Backpack, 2 large sacks, 6 torches, 3 oil flasks, tinderbox, "
        "10ft pole, 50ft rope, waterskin, 7 days rations, silver mirror"
    ),
    "Pack C": (
        "Backpack, 4 small sacks, 12 iron spikes, 50ft rope, waterskin, "
        "7 days rations + Holy Symbol (cleric), Thief tools (thief), "
        "or Holy Water (other classes)"
    ),
}

# Fallback bonus item for packs where a class has no pack_bonus_items entry
PACK_BONUS_DEFAULT: dict = {
    "Pack C": ("Holy Water (vial)", 1, 0.1),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_saving_throws(cls, level: int) -> dict:
    key = (cls, level)
    if key in SAVING_THROWS:
        return dict(SAVING_THROWS[key])
    available = [q for (c, q) in SAVING_THROWS if c == cls]
    if not available:
        return dict(CREATION_RULES[cls].default_saves)
    return dict(SAVING_THROWS[(cls, max(available))])


def get_spell_slots(cls, level: int) -> list:
    table = SPELL_SLOTS_BY_CLASS.get(cls)
    if table is None:
        return [0, 0, 0, 0, 0, 0]
    if level in table:
        return list(table[level])
    return list(table[max(table.keys())])
