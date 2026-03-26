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
#Number of light items can fit in a bundle by default.
BUNDLE_SIZE = 10

#Multipliers
POWER_LEVEL = 100
LEVEL_MULTIPLIER = 2

# Physical ranks E–A, arcane ranks V–Z (for staves, tomes, etc.)
_PHYSICAL_RANKS = ("E", "D", "C", "B", "A")
_ARCANE_RANKS   = ("V", "W", "X", "Y", "Z")


#Enums

class BundleData(StrEnum):
    MAX_SIZE = "maxSize"
    CONTENTS = "contents"

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

class ItemType(StrEnum):
    LIGHT_CONTAINER = "light_container"
    ITEM = "item"
    GEAR = "gear"
    WEAPON = "weapon"
    CHARGE_WEAPON = "charge_weapon"

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


# Maps Gear.slot strings (from items.json) and legacy hero.py slot values
# to the canonical ItemSlot used by the equip system.
# Weapons always go to MAIN_HAND and are handled separately in character.py.
GEAR_SLOT_MAP: dict[str, ItemSlot] = {
    # items.json values
    "head":      ItemSlot.HEAD,
    "body":      ItemSlot.BODY,
    "arms":      ItemSlot.ARMS,
    "legs":      ItemSlot.LEGS,        # items.json uses "legs" for boots/shoes
    "offhand":   ItemSlot.OFF_HAND,
    "accessory": ItemSlot.ACCESSORY1,  # fallback; engine picks ACC1 or ACC2
    # legacy hero.py values
    "mainhand":  ItemSlot.MAIN_HAND,
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
DEFAULT_EQUIPPED_SLOTS: dict[str, str | None] = {s: None for s in UI_SLOTS}


class SortMode(IntEnum):
    ALPHABETICAL = 0

class Stat(StrEnum):
    PHYSIQUE = 'physique'
    FINESSE = 'finesse'
    REASON = 'reason'
    SAVVY = 'savvy'

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
