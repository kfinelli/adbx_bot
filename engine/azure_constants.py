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

#TODO: keep or change? These are different than the Json names
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

class Slot(StrEnum):
    MAIN = 'mainhand'
    OFF = 'offhand'
    HEAD = 'head'
    BODY = 'body'
    ARMS = 'arms'
    LEGS = 'legs'
    ACCESSORY = 'accessory'

class SortMode (IntEnum):
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
