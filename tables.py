"""
Static B/X D&D reference tables.
All data is read-only. No game state, no I/O.
"""

from __future__ import annotations
from models import CharacterClass

# ---------------------------------------------------------------------------
# Ability score modifiers (B/X — non-standard, not 5e-style)
# ---------------------------------------------------------------------------

# Maps raw ability score to (modifier, note) for general attributes.
# STR affects melee to-hit and damage; DEX affects missile to-hit and AC;
# CON affects HP per die; INT affects languages; WIS affects saves vs magic;
# CHA affects reaction rolls and retainer morale.

ABILITY_MODIFIERS: dict[int, int] = {
    3:  -3,
    4:  -2,
    5:  -2,
    6:  -1,
    7:  -1,
    8:  -1,
    9:   0,
    10:  0,
    11:  0,
    12:  0,
    13:  1,
    14:  1,
    15:  1,
    16:  2,
    17:  2,
    18:  3,
}

# CON modifier specifically affects HP rolls (same scale in B/X)
CON_HP_MODIFIER = ABILITY_MODIFIERS  # alias for clarity


# ---------------------------------------------------------------------------
# Hit dice by class
# ---------------------------------------------------------------------------

HIT_DIE: dict[CharacterClass, int] = {
    CharacterClass.FIGHTER:    8,
    CharacterClass.THIEF:      4,
    CharacterClass.CLERIC:     6,
    CharacterClass.MAGIC_USER: 4,
    CharacterClass.ELF:        6,
    CharacterClass.DWARF:      8,
    CharacterClass.HALFLING:   6,
}


# ---------------------------------------------------------------------------
# Saving throws by class and level
# Keys: (CharacterClass, level) -> dict of save names to target numbers
# B/X saving throw categories:
#   death_poison, wands, paralysis_stone, breath_weapon, spells
# ---------------------------------------------------------------------------

_ST = "death_poison wands paralysis_stone breath_weapon spells".split()

def _saves(*values: int) -> dict[str, int]:
    return dict(zip(_ST, values))

SAVING_THROWS: dict[tuple[CharacterClass, int], dict[str, int]] = {
    # Fighter / Dwarf (same table in B/X)
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

    # Cleric
    (CharacterClass.CLERIC, 1):  _saves(11, 12, 14, 16, 15),
    (CharacterClass.CLERIC, 2):  _saves(11, 12, 14, 16, 15),
    (CharacterClass.CLERIC, 3):  _saves(11, 12, 14, 16, 15),
    (CharacterClass.CLERIC, 4):  _saves( 9, 10, 12, 14, 12),
    (CharacterClass.CLERIC, 5):  _saves( 9, 10, 12, 14, 12),
    (CharacterClass.CLERIC, 6):  _saves( 9, 10, 12, 14, 12),
    (CharacterClass.CLERIC, 7):  _saves( 7,  8, 10, 12, 10),
    (CharacterClass.CLERIC, 8):  _saves( 7,  8, 10, 12, 10),
    (CharacterClass.CLERIC, 9):  _saves( 7,  8, 10, 12, 10),
    (CharacterClass.CLERIC, 10): _saves( 5,  6,  8, 10,  8),
    (CharacterClass.CLERIC, 11): _saves( 5,  6,  8, 10,  8),
    (CharacterClass.CLERIC, 12): _saves( 5,  6,  8, 10,  8),
    (CharacterClass.CLERIC, 13): _saves( 3,  4,  6,  8,  6),
    (CharacterClass.CLERIC, 14): _saves( 3,  4,  6,  8,  6),

    # Magic-User
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

    # Thief
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

    # Elf (uses Magic-User saves)
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

    # Halfling (uses Fighter saves, max level 8)
    (CharacterClass.HALFLING, 1): _saves(10, 13, 12, 13, 15),
    (CharacterClass.HALFLING, 2): _saves(10, 13, 12, 13, 15),
    (CharacterClass.HALFLING, 3): _saves(10, 13, 12, 13, 15),
    (CharacterClass.HALFLING, 4): _saves( 8, 11, 10, 10, 13),
    (CharacterClass.HALFLING, 5): _saves( 8, 11, 10, 10, 13),
    (CharacterClass.HALFLING, 6): _saves( 8, 11, 10, 10, 13),
    (CharacterClass.HALFLING, 7): _saves( 6,  9,  8,  7, 10),
    (CharacterClass.HALFLING, 8): _saves( 6,  9,  8,  7, 10),
}

# Dwarf shares Fighter saves
for lvl in range(1, 15):
    SAVING_THROWS[(CharacterClass.DWARF, lvl)] = SAVING_THROWS[(CharacterClass.FIGHTER, lvl)]


# ---------------------------------------------------------------------------
# Spell slot progression (MU and Cleric)
# Index = spell level - 1; value = number of spells memorizable per day
# ---------------------------------------------------------------------------

# Magic-User spell slots by character level
MU_SPELL_SLOTS: dict[int, list[int]] = {
    1:  [1, 0, 0, 0, 0, 0],
    2:  [2, 0, 0, 0, 0, 0],
    3:  [2, 1, 0, 0, 0, 0],
    4:  [2, 2, 0, 0, 0, 0],
    5:  [2, 2, 1, 0, 0, 0],
    6:  [2, 2, 2, 0, 0, 0],
    7:  [3, 2, 2, 1, 0, 0],
    8:  [3, 3, 2, 2, 0, 0],
    9:  [3, 3, 3, 2, 1, 0],
    10: [3, 3, 3, 3, 2, 0],
    11: [4, 3, 3, 3, 2, 1],
    12: [4, 4, 3, 3, 3, 2],
    13: [4, 4, 4, 3, 3, 3],
    14: [4, 4, 4, 4, 3, 3],
}

# Cleric spell slots by character level
CLERIC_SPELL_SLOTS: dict[int, list[int]] = {
    1:  [0, 0, 0, 0, 0, 0],  # Clerics get no spells at level 1 in strict B/X
    2:  [1, 0, 0, 0, 0, 0],
    3:  [2, 0, 0, 0, 0, 0],
    4:  [2, 1, 0, 0, 0, 0],
    5:  [2, 2, 0, 0, 0, 0],
    6:  [2, 2, 1, 1, 0, 0],
    7:  [2, 2, 2, 1, 1, 0],
    8:  [3, 3, 2, 2, 1, 0],
    9:  [3, 3, 3, 2, 2, 0],
    10: [4, 4, 3, 3, 2, 0],
    11: [4, 4, 4, 3, 3, 0],
    12: [4, 4, 4, 4, 4, 0],
    13: [5, 5, 4, 4, 4, 0],
    14: [5, 5, 5, 4, 4, 0],
}

# Elf spell slots by character level (max level 10, MU-like progression)
ELF_SPELL_SLOTS: dict[int, list[int]] = {
    1:  [1, 0, 0, 0, 0, 0],
    2:  [2, 0, 0, 0, 0, 0],
    3:  [2, 1, 0, 0, 0, 0],
    4:  [2, 2, 0, 0, 0, 0],
    5:  [2, 2, 1, 0, 0, 0],
    6:  [2, 2, 2, 0, 0, 0],
    7:  [3, 2, 2, 1, 0, 0],
    8:  [3, 3, 2, 2, 0, 0],
    9:  [3, 3, 3, 2, 1, 0],
    10: [3, 3, 3, 3, 2, 0],
}

SPELL_SLOTS_BY_CLASS: dict[CharacterClass, dict[int, list[int]]] = {
    CharacterClass.MAGIC_USER: MU_SPELL_SLOTS,
    CharacterClass.CLERIC:     CLERIC_SPELL_SLOTS,
    CharacterClass.ELF:        ELF_SPELL_SLOTS,
}

# Non-spellcasting classes (used to check before building a SpellBook)
NON_CASTERS = {
    CharacterClass.FIGHTER,
    CharacterClass.THIEF,
    CharacterClass.DWARF,
    CharacterClass.HALFLING,
}


# ---------------------------------------------------------------------------
# Starting spells (Magic-Users begin with Read Magic + one random/chosen spell)
# Elves similarly. This list is just a reference for the DM / bot UI.
# ---------------------------------------------------------------------------

MU_STARTING_SPELLS = ["Read Magic"]  # guaranteed; player picks one more

# ---------------------------------------------------------------------------
# Equipment packages (index matches a menu choice presented to player)
# Each package is a list of (item_name, quantity, encumbrance_per_item)
# ---------------------------------------------------------------------------

EQUIPMENT_PACKAGES: dict[str, list[tuple[str, int, float]]] = {
    "Delver": [
        ("Torch",          6, 0.1),
        ("Rations (iron)", 3, 0.5),
        ("Rope (50')",     1, 1.0),
        ("Backpack",       1, 0.0),
        ("Waterskin",      1, 0.5),
        ("Dagger",         1, 0.5),
    ],
    "Warrior": [
        ("Torch",          3, 0.1),
        ("Rations (iron)", 3, 0.5),
        ("Backpack",       1, 0.0),
        ("Waterskin",      1, 0.5),
        ("Sword",          1, 1.0),
        ("Shield",         1, 1.0),
        ("Leather armor",  1, 2.0),
    ],
    "Scholar": [
        ("Torch",          6, 0.1),
        ("Rations (iron)", 3, 0.5),
        ("Backpack",       1, 0.0),
        ("Waterskin",      1, 0.5),
        ("Dagger",         1, 0.5),
        ("Spellbook",      1, 1.0),
        ("10' Pole",       1, 1.0),
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_saving_throws(cls: CharacterClass, level: int) -> dict[str, int]:
    """
    Return the saving throw target numbers for a class/level pair.
    Falls back to the highest available level entry if level exceeds the table.
    """
    key = (cls, level)
    if key in SAVING_THROWS:
        return dict(SAVING_THROWS[key])
    # Find highest defined level for this class
    available = [l for (c, l) in SAVING_THROWS if c == cls]
    if not available:
        raise ValueError(f"No saving throw data for class {cls}")
    return dict(SAVING_THROWS[(cls, max(available))])


def get_spell_slots(cls: CharacterClass, level: int) -> list[int]:
    """
    Return the spell slot list for a spellcasting class at a given level.
    Returns a zeroed list for non-casters.
    """
    table = SPELL_SLOTS_BY_CLASS.get(cls)
    if table is None:
        return [0, 0, 0, 0, 0, 0]
    if level in table:
        return list(table[level])
    # Clamp to highest defined level
    return list(table[max(table.keys())])
