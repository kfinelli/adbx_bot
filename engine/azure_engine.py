from enum import Enum

from engine.azure_constants import JOBS, POWER_LEVEL, Stat
from engine.azure_helpers import d
from engine.hero import Character


def rollStats():
    dieSize = 4 * POWER_LEVEL
    penalty = 5 * POWER_LEVEL
    baseStats = {
        Stat.PHYSIQUE: 0,
        Stat.FINESSE: 0,
        Stat.REASON: 0,
        Stat.SAVVY: 0
    }
    for stat in baseStats:
        baseStats[stat] = d(dieSize) + d(dieSize) - penalty
    return baseStats


def createCharacter(name, stats, jobID):
    char = Character(name)
    char.baseStats = stats
    char.addNewJob(jobID, True)
    char.refreshSheet(True)

    return char

def loadCharacterFromJSON(jsonData):
    pass


"""
Status Classes
><><><><><><><><><><><><><
Common Types of Status should probably have their own class, just to make sure we know what components are for sure a
part of them. I don't know how granular we want to get with it, however.
"""

class Status:
    def __init__(self, id, displayName, type, description):
        self.id = id
        self.displayName = displayName
        self.type = type
        self.description = description
    def on_add(self):
        pass
    def on_remove(self):
        pass

class StatChangeStatus(Status):
    def __init__(self, id, displayName, type, description, stat=None, value=0, duration=-1):
        super().__init__(id, displayName, type, description)
        self.stat = stat
        self.value = value
        self.duration = duration

# ---------------------------------------------------------------------------
# Engine integration — CharacterClass enum and CREATION_RULES
# ---------------------------------------------------------------------------
# These are needed by models.py, engine/character.py, and the serialisation
# layer.  The CharacterClass enum is generated automatically from the loaded
# job files so adding a new job only requires dropping a JSON file in
# data/classes/ — no Python changes needed.
#
# The circular import  models.py → azure_tables → engine/data_loader → models
# is avoided by loading jobs here (importJobs) rather than going through
# engine/__init__.  azure_tables has no dependency on models.py.
# ---------------------------------------------------------------------------
# CharacterClass enum: member name = uppercase job id, value = display name
# e.g. CharacterClass.KNIGHT.value == "Knight"

CharacterClass = Enum(
    "CharacterClass",
    {job.id.upper(): job.displayName for job in JOBS.values()},
)

# CREATION_RULES: CharacterClass member → Job
# Provides the same interface that engine/character.py and other modules
# currently expect from tables.CREATION_RULES.

CREATION_RULES = {
    cls: JOBS[cls.name.lower()] for cls in CharacterClass
}

# Backward-compatibility stubs — will be removed once Stream B completes.
ABILITY_MODIFIERS: dict  = {}   # replaced by get_stat_modifier()
CON_HP_MODIFIER          = ABILITY_MODIFIERS
SAVING_THROWS: dict      = {}
SPELL_SLOTS_BY_CLASS: dict = {}
EQUIPMENT_PACKAGES: dict   = {}
EQUIPMENT_PACKAGE_DESCRIPTIONS: dict = {}
PACK_BONUS_DEFAULT: dict   = {}


def get_saving_throws(cls, level: int) -> dict:
    """Stub: saving throws are now a single base_save integer per job."""
    return {}


def get_spell_slots(cls, level: int) -> list:
    """Stub: spell slots not yet implemented in the Azure ruleset."""
    return []
