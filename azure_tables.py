import json
import math
import random
from enum import Enum
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
_PROJECT_DIR = Path(__file__).parent
_CLASSES_DIR = _PROJECT_DIR / "data" / "classes"

#Character Constants
MAX_LEVEL = 99
BASE_INVENTORY_SIZE = 6

#Multipliers
POWER_LEVEL = 100
LEVEL_MULTIPLIER = 2

# ---------------------------------------------------------------------------
# Weapon rank helpers
# ---------------------------------------------------------------------------
# Physical ranks E–A, arcane ranks V–Z (for staves, tomes, etc.)
_PHYSICAL_RANKS = ("E", "D", "C", "B", "A")
_ARCANE_RANKS   = ("V", "W", "X", "Y", "Z")

def getLowerWeaponRanks(rank):
    lowerweaponranks = {
        "A": {"E","D","C","B","A"},
        "B": {"E","D","C","B"},
        "C": {"E","D","C"},
        "D": {"E","D"},
        "E": {"E"},
        "V": {"V"},
        "W": {"V","W"},
        "X": {"V","W","X"},
        "Y": {"V","W","X","Y"},
        "Z": {"V","W","X","Y","Z"},
    }
    if rank not in lowerweaponranks:
        return {"E"}
    return lowerweaponranks[rank]

# ---------------------------------------------------------------------------
# Stat helpers added for engine integration
# ---------------------------------------------------------------------------

def get_stat_modifier(stat_value: int) -> int:
    """
    Convert a scaled stat value to its effective modifier. Needed for some
    backward compatibility during transition. Simply returns the stat (since we
    use the stat itself to modify rolls in this system)
    """
    return stat_value

def get_inventory_size(physique: int) -> int:
    """
    Calculate inventory slot count from a character's scaled Physique stat.
    Base slots + floor(physique / POWER_LEVEL), minimum 1.
    """
    bonus = math.floor(physique / POWER_LEVEL)
    return max(1, BASE_INVENTORY_SIZE + bonus)

def get_stat_growth_die(job, stat) -> int:
    """
    Return the die size for stat growth on level-up for the given Job and Stat.
    Die size = StatPriority.value * POWER_LEVEL.
    A NONE priority means this stat never grows from this job (returns 0).
    """
    priority = job.statPriority.get(stat, StatPriority.NONE)
    return priority.value * POWER_LEVEL

def getJobFromID(jobID):
    return JOBS[jobID]

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

class Slot(Enum):
    MAIN = 0
    OFF = 1
    HEAD = 2
    BODY = 3
    ARMS = 4
    LEGS = 5
    ACCESSORY = 6

class Stat(Enum):
    PHYSIQUE = 0
    FINESSE = 1
    REASON = 2
    SAVVY = 3

class StatPriority(Enum):
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

"""
Jobs and Skills
><><><><><><><><><><><><><
Common Types of Status should probably have their own class, just to make sure we know what components are for sure a
part of them. I don't know how granular we want to get with it, however.
"""

class Job:
    def __init__(self, id, displayName, hitDie, weaponRanks, baseSave, primaryStat,
                 skills=None, description=None, maxLevel=5):
        if skills is None:
            skills = {}
        self.id = id
        self.displayName = displayName
        self.description = description
        self.hitDie = hitDie
        self.weaponRanks = weaponRanks
        self.baseSave = baseSave
        self.primaryStat = primaryStat      # "PHY" | "FNS" | "RSN" | "SVY"
        self.skills = skills
        self.maxLevel = maxLevel
        # Derive stat priority from primary stat:
        # primary stat grows at GREATEST; others don't grow from this job.
        _stat_key = {
            "PHY": Stat.PHYSIQUE,
            "FNS": Stat.FINESSE,
            "RSN": Stat.REASON,
            "SVY": Stat.SAVVY,
        }
        primary = _stat_key.get(primaryStat, Stat.PHYSIQUE)
        self.statPriority = {
            Stat.PHYSIQUE: StatPriority.GREATEST if primary == Stat.PHYSIQUE else StatPriority.NONE,
            Stat.FINESSE:  StatPriority.GREATEST if primary == Stat.FINESSE  else StatPriority.NONE,
            Stat.REASON:   StatPriority.GREATEST if primary == Stat.REASON   else StatPriority.NONE,
            Stat.SAVVY:    StatPriority.GREATEST if primary == Stat.SAVVY    else StatPriority.NONE,
        }

class JobExperience:
    def __init__(self, jobID, level=0, health=0, ranks = None):
        if ranks is None:
            ranks = {'e'}
        self.jobID = jobID
        self.level = level
        self.health = health
        self.weaponRanks = ranks
        self.skills = {}
        self.statChanges = {
            Stat.PHYSIQUE: 0,
            Stat.FINESSE: 0,
            Stat.REASON: 0,
            Stat.SAVVY: 0
        }
        self.rebuildSkills()

    def getJob(self):
        return getJobFromID(self.jobID)

    def rebuildSkills(self):
        job = self.getJob()
        skills = {}
        for skillName in job.skills:
            skill = job.skills[skillName]
            if skill.level <= self.level:
                skills[skill.id] = skill
        self.skills = skills

    def levelUpJob(self):
        job = self.getJob()
        changes = {
            Stat.PHYSIQUE: 0,
            Stat.FINESSE: 0,
            Stat.REASON: 0,
            Stat.SAVVY: 0
        }
        for stat in changes:
            changeDie = job.statPriority[stat].value
            changes[stat] += d(changeDie * POWER_LEVEL)/100
            self.statChanges[stat] += changes[stat]
        changes['health'] = d(job.hitDie * POWER_LEVEL)
        self.health += changes['health']
        self.level += 1
        newSkills = ""
        for skillName in job.skills:
            skill = job.skills[skillName]
            if skill.level > self.level:
                pass
            if skill.level == self.level:
                self.skills[skill.id] = skill
                newSkills += skill.name + "\n"
            if skill.type is SkillType.WEAPON_RANK:
                self.weaponRanks.update(skill.rank)
        changes['skills'] = newSkills
        return changes


class Skill:
    def __init__(self, id, name, source, level, type, description=""):
        self.id = id
        self.name = name
        self.source = source
        self.level = level
        self.type = type
        self.description = description
        self.dm_notes = ""

def createSkillFromJson(name, sData):
    skillType = SkillType(sData['type'])
    newSkill = Skill(sData['id'], name, sData['source'], sData['level'] * LEVEL_MULTIPLIER, skillType, sData.get('desc', ''))
    if 'dm_notes' in sData:
        newSkill.dm_notes = sData['dm_notes']
    if skillType == SkillType.WEAPON_RANK:
        rank = sData['rank']
        if 'desc' not in sData:
            newSkill.description = "You can equip gear and weapons with Rank: " + rank
        newSkill.rank = rank
    if skillType == SkillType.PASSIVE_BONUS:
        baseBonus = 1
        if sData['stat'] == "SAVE":
            newSkill.stat = 0
            newSkill.bonus = sData['bonus']
            return newSkill
        if sData['stat'] == "PHY":
            newSkill.stat = Stat.PHYSIQUE
        elif sData['stat'] == "FNS":
            newSkill.stat = Stat.FINESSE
        elif sData['stat'] == "RSN":
            newSkill.stat = Stat.REASON
        elif sData['stat'] == "SVY":
            newSkill.stat = Stat.SAVVY
        else:
            newSkill.stat = -1
        newSkill.bonus = baseBonus * POWER_LEVEL
    return newSkill

# ---------------------------------------------------------------------------
# Per-file loaders — reads from data/classes/<key>.json and
# data/classes/<key>_skills.json, one file per job.
# This replaces the original monolithic Jobs.json / JobSkills.json approach
# so that jobs can be added simply by dropping a new file in the directory.
# ---------------------------------------------------------------------------

def _load_job_skills_file(skills_path: Path) -> dict:
    """
    Load a <key>_skills.json companion file and return a dict of
    skill_id -> Skill.  Returns {} if the file doesn't exist.
    """
    if not skills_path.exists():
        return {}
    with open(skills_path, encoding="utf-8") as f:
        skilljson = json.load(f)
    skillData = {}
    for skillName, sData in skilljson.items():
        newSkill = createSkillFromJson(skillName, sData)
        skillData[newSkill.id] = newSkill
    return skillData

def _load_job_file(job_path: Path) -> "Job":
    """
    Load a single data/classes/<key>.json file and return a Job.
    The JSON uses snake_case keys matching the Azure job schema.
    Skills are loaded from the companion <key>_skills.json if present.
    """
    with open(job_path, encoding="utf-8") as f:
        cJob = json.load(f)
    skills_path = job_path.parent / (job_path.stem + "_skills.json")
    skills = _load_job_skills_file(skills_path)
    weaponRanks = getLowerWeaponRanks(cJob['weapon_rank'])
    # 'key' is the uppercase identifier; job id is its lowercase form
    job_id = cJob['key'].lower()
    return Job(
        job_id,
        cJob['display_name'],
        cJob['hit_die'],
        weaponRanks,
        cJob['base_save'],
        cJob['primary_stat'],
        skills,
        cJob.get('description', ''),
        cJob['max_level'] * LEVEL_MULTIPLIER,
    )

def importJobs(classes_dir: Path = _CLASSES_DIR) -> dict:
    """
    Load all jobs from data/classes/*.json.
    Returns a dict keyed by job id (e.g. "knight").
    Companion <key>_skills.json files are loaded automatically.
    """
    jobData = {}
    if not classes_dir.exists():
        return jobData
    for path in sorted(classes_dir.glob("*.json")):
        if path.stem.endswith("_skills"):
            continue   # companion skill files are handled by _load_job_file
        job = _load_job_file(path)
        jobData[job.id] = job
    return jobData

"""
Items and Equipment
><><><><><><><><><><><><><
"""

class Item:
    def __init__(self, name, description = "", isLight = False):
        self.name = name
        self.description = description
        self.isLight = False

    def setName(self, name):
        self.name = name
    def setDescription(self, description):
        self.description = description
    def setLightness(self, isLight):
        self.isLight = isLight

class EquipItem(Item):
    def __init__(self, name, rank, tags=None, otherAbilities=None, description="", isLight=False):
        super().__init__(name, description, isLight)
        if tags is None:
            tags = []
        self.rank = rank
        self.tags = tags
        self.otherAbilities = otherAbilities

    def setRank (self, rank):
        self.rank = rank
    def setOtherAbilities(self, otherAbilities):
        self.otherAbilities = otherAbilities

    def addTag(self, tag):
        self.tags.append(tag)
    def removeTag(self, tag):
        self.tags.remove(tag)
    def getTags(self):
        return self.tags

    def onEquip(self):
        pass
    def onUnequip(self):
        pass

class Weapon(EquipItem):
    def __init__(self, name, rank, type, stat, damage, range=0, tags = None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, tags, otherAbilities, description, isLight)
        self.type = type
        self.stat = stat
        self.damage = max(0, damage)
        self.range = max(0, range)

    def setType(self, type):
        self.type = type

    def setStat(self, stat):
        if stat not in Stat:
            pass
        self.stat = stat
    def setRange(self, range):
        self.range = max(0, range)

    def setDamage(self, damage):
        self.damage = max(0, damage)
    def changeDamage(self, deltaDamage):
        self.damage = max(0, self.damage - deltaDamage)

class Gear(EquipItem):
    def __init__(self, name, slot, rank, health, defense, resistance, tags=None, otherAbilities=None, description="", isLight = False):
        super().__init__(name, rank, tags, otherAbilities, description, isLight)
        self.slot = slot
        self.health = health
        self.defense = defense
        self.resistance = resistance

"""
Character Sheet & Generation
><><><><><><><><><><><><><
This is gonna get modified quite a bit, probably
"""

class Character:
    def __init__(self, name):
        #"Constants"
        self.name = name
        self.experience = 0
        self.level = 0
        self.physique = 0
        self.finesse = 0
        self.reason = 0
        self.savvy = 0
        self.baseSave = 0
        self.weaponRank = {"E"}
        self.maxHealth = 0

        #"Variables"
        self.health = 0

        # Contains JobExperience for all Jobs
        self.jobs = {}

        #Stat Stuff
        self.baseStats = {
            Stat.PHYSIQUE: 0,
            Stat.FINESSE: 0,
            Stat.REASON: 0,
            Stat.SAVVY: 0,
        }

        #Dictionary Keys should be the skill's ID.
        self.skills = {
            SkillType.SIMPLE: {},
            SkillType.TURN_ACTION: {},
            SkillType.COMBAT_ACTION: {},
            SkillType.ORACLE_ACTION: {},
            SkillType.FREE_ACTION: {},
            SkillType.PASSIVE_BONUS: {},
            SkillType.WEAPON_RANK: {},
            SkillType.ROLEPLAY: {},
            SkillType.STATUS: {},
            SkillType.COMPLEX: {}
        }

        #Dictionary Keys should be the status's ID.
        self.status = {
            StatusType.PERMANENT: {},
            StatusType.ON_DEATH: {},
            StatusType.TURN_START: {},
            StatusType.TURN_END: {},
            StatusType.SHORT: {},
            StatusType.LONG: {}
        }

        #Inventory
        #    &
        #Equipment
        self.invSize = 0
        #Currently Equipped Gear
        self.equipment = {
            Slot.HEAD : None,
            Slot.BODY: None,
            Slot.ARMS: None,
            Slot.LEGS : None,
            Slot.MAIN: None,
            Slot.OFF: None,
            Slot.ACCESSORY: []
        }

        #Items
        self.inventory = []

    def addNewJob(self, jobID, isFirst=False):
        job = getJobFromID(jobID)
        health = d(job.hitDie * POWER_LEVEL)
        if (isFirst):
            health = job.hitDie * POWER_LEVEL
            self.baseSave = job.baseSave * POWER_LEVEL
        jobExperience = JobExperience(job.id, 1, health, job.weaponRanks)
        self.jobs[job.id] = jobExperience

    def levelUp(self, jobID):
        if jobID in self.jobs:
            self.jobs[jobID].levelUpJob()
        else:
            self.addNewJob(jobID)
        self.refreshSheet(True)

    def refreshSheet(self, heal=False):
        self.recalculateAllStats()
        self.recalculateInventorySize()
        self.recalculateMaxHealth()
        self.recalculateWeaponRank()
        self.rebuildSkills()
        if heal:
            self.health = self.maxHealth

    def rebuildSkills(self):
        skills = {
            SkillType.SIMPLE: {},
            SkillType.TURN_ACTION: {},
            SkillType.COMBAT_ACTION: {},
            SkillType.ORACLE_ACTION: {},
            SkillType.FREE_ACTION: {},
            SkillType.PASSIVE_BONUS: {},
            SkillType.WEAPON_RANK: {},
            SkillType.ROLEPLAY: {},
            SkillType.STATUS: {},
            SkillType.COMPLEX: {}
        }

        for job in self.jobs:
            cJob = self.jobs[job]
            for skill in cJob.skills:
                cSkill = cJob.skills[skill]
                skills[cSkill.type][skill] = cSkill
        self.skills = skills

    def recalculateAllStats(self):
        totals = self.baseStats
        for job in self.jobs:
            for stat in Stat:
                totals[stat] += self.jobs[job].statChanges[stat]
        self.physique = totals[Stat.PHYSIQUE]
        self.finesse = totals[Stat.FINESSE]
        self.reason = totals[Stat.REASON]
        self.savvy = totals[Stat.SAVVY]

    def recalculateInventorySize(self):
        strBonus = self.physique/100
        if (strBonus > 0):
            strBonus = math.floor(strBonus)
        elif (strBonus < 0):
            strBonus = math.ceil(strBonus)
        total = BASE_INVENTORY_SIZE + strBonus
        self.invSize = total


    def recalculateMaxHealth(self):
        total = 0
        for job in self.jobs:
            total += self.jobs[job].health
        self.maxHealth = total

    def recalculateWeaponRank(self):
        weaponRank = set()
        for job in self.jobs:
            ranks = self.jobs[job].weaponRanks
            weaponRank.update(ranks)
        self.weaponRank = weaponRank

    def toDictionary(self):
        charSheet = {
            'name': self.name,
            'experience': self.experience,
            'level': self.level,
            'physique': self.physique,
            'finesse': self.finesse,
            'reason': self.reason,
            'savvy': self.savvy,
            'baseSave': self.baseSave,
            'weaponRank': self.weaponRank,
            'jobs': self.jobs,
            'baseStats': self.baseStats,
            'status': self.status,
            'inventorySize': self.invSize,
            'equipment': self.equipment,
            'inventory': self.inventory,
            'maxHealth': self.maxHealth,
            'health': self.health
        }
        return charSheet

    def export(self):
        return json.dumps(self.toDictionary())

    def addItem(self, item):
            if len(self.inventory) >= self.invSize:
                return
            self.inventory.append(item)

    def removeItem(self, item):
        self.inventory.remove(item)

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

def d(x):
    """Roll a die with x sides. Returns 0 for x < 1 (used for NONE stat priority)."""
    x = int(x)
    if x < 1:
        return 0
    return random.randint(1, x)

JOBS = importJobs()

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
