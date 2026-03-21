from enum import Enum
import json
import math
import random

"""
Enums, Constants, and Utils
><><><><><><><><><><><><><
Enums are preferable to raw strings where applicable. 
"""

'''
MAYBE MOVE THIS TO A CONFIG FILE EVENTUALLY
'''
#File Paths
JOB_FILE = "resources/Jobs/Jobs.json"
SKILL_FILE = "resources/Jobs/JobSkills.json"

#Character Constants
MAX_LEVEL = 99
BASE_INVENTORY_SIZE = 6

#Multipliers
POWER_LEVEL = 100
LEVEL_MULTIPLIER = 2

def d(x):
    if (x < 1):
        return 0
    return random.randint(1, int(x))

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
    def __init__(self, id, displayName, hitDie, weaponRanks, baseSave, skills = None, description = None, maxLevel = 5):
        if skills is None:
            skills = dict()
        self.id = id
        self.displayName = displayName
        self.description = description
        self.hitDie = hitDie
        self.weaponRanks = weaponRanks
        self.baseSave = baseSave
        self.skills = skills
        self.maxLevel = maxLevel
        self.statPriority = {
            Stat.PHYSIQUE: StatPriority.NONE,
            Stat.FINESSE: StatPriority.NONE,
            Stat.REASON: StatPriority.NONE,
            Stat.SAVVY: StatPriority.NONE,
        }

class JobExperience:
    def __init__(self, jobID, level=0, health=0, ranks = {'e'}):
        self.jobID = jobID
        self.level = level
        self.health = health
        self.weaponRanks = ranks
        self.skills = dict()
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
        skills = dict()
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

def getLowerWeaponRanks(rank):
    if rank == "A":
        return {"E","D","C","B","A"}
    elif rank == "B":
        return{"E","D","C","B"}
    elif rank == "C":
        return{"E","D","C"}
    elif rank == "D":
        return{"E","D"}
    return{"E"}

def createSkillFromJson(name, sData):
    skillType = SkillType(sData['type'])
    newSkill = Skill(sData['id'], name, sData['source'], sData['level'] * LEVEL_MULTIPLIER, skillType, sData['desc'])
    if 'dm_notes' in sData:
        newSkill.dm_notes = sData['dm_notes']
    if skillType == SkillType.WEAPON_RANK:
        rank = sData['rank']
        if 'desc' not in sData:
            newSkill.description = "You can equip gear and weapons with Rank: " + rank
        newSkill.rank = rank
    if skillType == SkillType.PASSIVE_BONUS:
        baseBonus = 1
        stat = None
        if sData['stat'] is "SAVE":
            newSkill.stat = 0
            newSkill.bonus = sData['bonus']
            return newSkill
        if sData['stat'] is "PHY":
            newSkill.stat = Stat.PHYSIQUE
        elif sData['stat'] is "FNS":
            newSkill.stat = Stat.FINESSE
        elif sData['stat'] is "RSN":
            newSkill.stat = Stat.REASON
        elif sData['stat'] is "SVY":
            newSkill.stat = Stat.SAVVY
        else:
            newSkill.stat = -1
        newSkill.bonus = baseBonus * POWER_LEVEL
    return newSkill

def importJobSkills():
    jobSkillFile = open(SKILL_FILE)
    skilljson = json.load(jobSkillFile)
    jobSkillData = dict()
    for job in skilljson:
        jobSkillData[job] = dict()
        for skill in skilljson[job]:
            newSkill = createSkillFromJson(skill, skilljson[job][skill])
            jobSkillData[job][newSkill.id] = newSkill
    return jobSkillData

def importJobs():
    jobFile = open(JOB_FILE)
    jobson = json.load(jobFile)
    jobData = dict()
    skillData = importJobSkills()
    for job in jobson:
        cJob = jobson[job]
        cWeaponRanks = getLowerWeaponRanks(cJob['weapon_rank'])
        cSkills = skillData[cJob['id']]
        skills = dict()
        for skill in cSkills:
            skills[skill] = (cSkills[skill])
        newJob = Job(cJob['id'], job, cJob['hit_die'], cWeaponRanks, cJob['base_save'], skills, cJob['desc'], cJob['max_level'] * LEVEL_MULTIPLIER)
        jobData[newJob.id] = newJob
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
            tags = list()
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
        self.jobs = dict()

        #Stat Stuff
        self.baseStats = {
            Stat.PHYSIQUE: 0,
            Stat.FINESSE: 0,
            Stat.REASON: 0,
            Stat.SAVVY: 0,
        }

        #Dictionary Keys should be the skill's ID.
        self.skills = {
            SkillType.SIMPLE: dict(),
            SkillType.TURN_ACTION: dict(),
            SkillType.COMBAT_ACTION: dict(),
            SkillType.ORACLE_ACTION: dict(),
            SkillType.FREE_ACTION: dict(),
            SkillType.PASSIVE_BONUS: dict(),
            SkillType.WEAPON_RANK: dict(),
            SkillType.ROLEPLAY: dict (),
            SkillType.STATUS: dict (),
            SkillType.COMPLEX: dict()
        }

        #Dictionary Keys should be the status's ID.
        self.status = {
            StatusType.PERMANENT: dict(),
            StatusType.ON_DEATH: dict(),
            StatusType.TURN_START: dict(),
            StatusType.TURN_END: dict(),
            StatusType.SHORT: dict(),
            StatusType.LONG: dict()
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
            Slot.ACCESSORY: list()
        }

        #Items
        self.inventory = list()

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
            SkillType.SIMPLE: dict(),
            SkillType.TURN_ACTION: dict(),
            SkillType.COMBAT_ACTION: dict(),
            SkillType.ORACLE_ACTION: dict(),
            SkillType.FREE_ACTION: dict(),
            SkillType.PASSIVE_BONUS: dict(),
            SkillType.WEAPON_RANK: dict(),
            SkillType.ROLEPLAY: dict(),
            SkillType.STATUS: dict(),
            SkillType.COMPLEX: dict()
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

JOBS = importJobs()