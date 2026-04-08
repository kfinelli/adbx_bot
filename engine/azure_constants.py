from enum import Enum, IntEnum, StrEnum
from pathlib import Path

"""
Enums, Constants, and Utils
><><><><><><><><><><><><><
Enums are preferable to raw strings where applicable.
"""

# ---------------------------------------------------------------------------
# Data directory — resolved relative to this file so the module works
# regardless of the working directory the server is launched from.
# ---------------------------------------------------------------------------
_PROJECT_DIR = Path(__file__).parent.parent
_CLASSES_DIR = _PROJECT_DIR / "data" / "classes"

#Character Constants
MAX_LEVEL = 99
BASE_INVENTORY_SIZE = 6

# XP required to reach each level (index = target level - 1).
# e.g. XP_THRESHOLDS[1] = 2000 means 2000 XP needed to reach level 2.
# Based on B/X D&D fighter progression; exact values to be tuned.
XP_THRESHOLDS: list[int] = [0, 2000, 4000, 8000, 16000, 32000]

DEFAULT_ROOM_XP: int = 100   # XP per character for entering an unvisited room

#Number of light items can fit in a bundle by default.
BUNDLE_SIZE = 10

#Multipliers
POWER_LEVEL = 100
LEVEL_MULTIPLIER = 2

# Physical ranks E–A, arcane ranks V–Z (for staves, tomes, etc.)
_PHYSICAL_RANKS = ("E", "D", "C", "B", "A")
_ARCANE_RANKS   = ("V", "W", "X", "Y", "Z")


#Enums

class ItemData(StrEnum):
    PROTOTYPE = "prototype"
    ITEM_ID = "item_id"
    NAME = "name"
    DESCRIPTION = "description"
    IS_LIGHT = "is_light"
    TAGS = "tags"
    OTHER_ABILITIES = "other_abilities"
    ATTACK_STATUS = "attack_status"
    HELD_STATUS = "held_status"
    RANK = "rank"
    ITEM_TYPE = "item_type"
    TYPE = "type"
    STAT = "stat"
    DAMAGE = "damage"
    RANGE = "range"
    CHARGES = "charges"
    MAX_CHARGES = "max_charges"
    RECHARGE_PERIOD = "recharge_period"
    DESTROY_ON_EMPTY = "destroy_on_empty"
    SLOT = "slot"
    HEALTH = "health"
    DEFENSE = "defense"
    RESISTANCE = "resistance"
    PURCHASEABLE = "purchaseable"
    PRICE = "price"
    CONTAINED_ITEMS = "contained_items"
    TARGETS_STAT = "targets_stat"

class ItemType(StrEnum):
    ITEM = "item"
    GEAR = "gear"
    WEAPON = "weapon"
    CHARGE_WEAPON = "charge_weapon"
    CONTAINER = "container"

class SkillType(Enum):
    SIMPLE = 0
    TURN_ACTION = 1
    COMBAT_ACTION = 2
    ORACLE_ACTION = 3
    FREE_ACTION = 4
    PASSIVE_BONUS = 5
    WEAPON_RANK = 6
    ROLEPLAY = 7
    STATUS = 8
    COMPLEX = 9


class ItemSlot(StrEnum):
    """
    Canonical equipment slot enum for the Azure ruleset.

    Merges the old ``Slot`` class into a single authoritative definition.

    Primary slot names (used by the equip system and character sheet):
        MAIN_HAND, OFF_HAND, HEAD, BODY, ARMS, LEGS, ACCESSORY1, ACCESSORY2

    All primary slots hold at most one item.  The two accessory slots are
    independent so a character can wear two accessories simultaneously.
    """
    # Primary slots (equip system)
    MAIN_HAND   = "main_hand"    # primary weapon / staff / rod
    OFF_HAND    = "off_hand"     # shield, torch, off-hand weapon
    HEAD        = "head"         # helmet, hat, crown
    BODY        = "body"         # chest armour, robes
    ARMS        = "arms"         # gauntlets, bracers
    LEGS        = "legs"         # boots, shoes
    ACCESSORY1  = "accessory1"   # ring, amulet, etc.
    ACCESSORY2  = "accessory2"   # second ring / accessory


# Backwards-compatibility alias so existing `from engine.azure_constants import Slot`
# imports (e.g. in hero.py) continue to work without modification.
Slot = ItemSlot


# Maps the slot string on any EquipItem (Weapon, Gear, ContainerItem) to the
# canonical ItemSlot used by the equip system.
# "accessory" is intentionally absent — it's handled separately (auto-pick ACC1/ACC2).
GEAR_SLOT_MAP: dict[str, ItemSlot] = {
    # canonical underscore form (items.json / new data)
    "main_hand": ItemSlot.MAIN_HAND,
    "off_hand":  ItemSlot.OFF_HAND,
    "head":      ItemSlot.HEAD,
    "body":      ItemSlot.BODY,
    "arms":      ItemSlot.ARMS,
    "legs":      ItemSlot.LEGS,
    # legacy / alternative spellings
    "mainhand":  ItemSlot.MAIN_HAND,
    "offhand":   ItemSlot.OFF_HAND,
}

# Slots that accept more than one item (auto-filled in order).
ACCESSORY_SLOTS: tuple[ItemSlot, ...] = (ItemSlot.ACCESSORY1, ItemSlot.ACCESSORY2)

# Ordered list of primary slots shown in the /character equip/unequip UI.
UI_SLOTS: tuple[ItemSlot, ...] = (
    ItemSlot.MAIN_HAND,
    ItemSlot.OFF_HAND,
    ItemSlot.HEAD,
    ItemSlot.BODY,
    ItemSlot.ARMS,
    ItemSlot.LEGS,
    ItemSlot.ACCESSORY1,
    ItemSlot.ACCESSORY2,
)

# Default equipped_slots dict for a new Character.
# Only primary slots are included; legacy aliases are item-data tags, not slots.
DEFAULT_EQUIPPED_SLOTS: dict[str, str | None] = dict.fromkeys(UI_SLOTS)


class Stat(StrEnum):
    PHYSIQUE = 'physique'
    FINESSE = 'finesse'
    REASON = 'reason'
    SAVVY = 'savvy'

class CombatStat(StrEnum):
    PHYSIQUE = 'physique'
    FINESSE = 'finesse'
    REASON = 'reason'
    SAVVY = 'savvy'
    MAX_HEALTH = 'max_health'
    DODGE = 'dodge'
    DEFENSE = 'defense'
    RESISTANCE = 'resistance'

class StatPriority(IntEnum):
    NONE = 0
    LEAST = 5
    LESSER = 10
    AVERAGE = 15
    GREATER = 20
    GREATEST = 25

class StatusType(Enum):
    PERMANENT = 0
    ON_DEATH = 1
    TURN_START = 2
    TURN_END = 3
    SHORT = 4
    LONG = 5

class RechargePeriod(StrEnum):
    DAY = 'day'
    ENCOUNTER = 'encounter'
    INFINITE = 'infinite'
    NEVER = 'never'
