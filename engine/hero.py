import json
import math

from engine.azure_constants import (
    BASE_INVENTORY_SIZE,
    POWER_LEVEL,
    ItemSlot,
    SkillType,
    Stat,
    StatusType,
)
from engine.azure_helpers import getJobFromID
from engine.dice import d

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
            ItemSlot.HEAD : None,
            ItemSlot.BODY: None,
            ItemSlot.ARMS: None,
            ItemSlot.LEGS : None,
            ItemSlot.MAIN_HAND: None,
            ItemSlot.OFF_HAND: None,
            ItemSlot.ACCESSORY: []
        }

        #Items
        self.inventory = []

    def addNewJob(self, jobID, isFirst=False):  # Not yet implemented
        job = getJobFromID(jobID)
        health = d(job.hitDie * POWER_LEVEL)
        if (isFirst):
            health = job.hitDie * POWER_LEVEL
            self.baseSave = job.baseSave * POWER_LEVEL
        jobExperience = JobExperience(job.id, 1, health, job.weaponRanks)
        self.jobs[job.id] = jobExperience

    def levelUp(self, jobID):  # Implemented in engine/character.py:433
        if jobID in self.jobs:
            self.jobs[jobID].levelUpJob()
        else:
            self.addNewJob(jobID)
        self.refreshSheet(True)

    def refreshSheet(self, heal=False):  # Not yet implemented elsewhere
        self.recalculateAllStats()
        self.recalculateInventorySize()
        self.recalculateMaxHealth()
        self.recalculateWeaponRank()
        self.rebuildSkills()
        if heal:
            self.health = self.maxHealth

    def rebuildSkills(self):  # Not yet implemented elsewhere
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

    def recalculateAllStats(self):  # Not yet implemented elsewhere
        totals = self.baseStats
        for job in self.jobs:
            for stat in Stat:
                totals[stat] += self.jobs[job].statChanges[stat]
        self.physique = totals[Stat.PHYSIQUE]
        self.finesse = totals[Stat.FINESSE]
        self.reason = totals[Stat.REASON]
        self.savvy = totals[Stat.SAVVY]

    def recalculateInventorySize(self):  # Implemented at models.py:196
        strBonus = self.physique/100
        if (strBonus > 0):
            strBonus = math.floor(strBonus)
        elif (strBonus < 0):
            strBonus = math.ceil(strBonus)
        total = BASE_INVENTORY_SIZE + strBonus
        self.invSize = total


    def recalculateMaxHealth(self):  # Not yet implemented elsewhere
        total = 0
        for job in self.jobs:
            total += self.jobs[job].health
        self.maxHealth = total

    def recalculateWeaponRank(self):  # Not yet implemented elsewhere
        weaponRank = set()
        for job in self.jobs:
            ranks = self.jobs[job].weaponRanks
            weaponRank.update(ranks)
        self.weaponRank = weaponRank

    def toDictionary(self):  # Implemented at serialization.py:85
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

    def export(self):  # Implemented at serialization.py:85
        return json.dumps(self.toDictionary())

    def addItem(self, item):  # Implemented at engine/character.py:236
            if len(self.inventory) >= self.invSize:
                return
            self.inventory.append(item)

    def removeItem(self, item):  # Implemented at engine/character.py:293
        self.inventory.remove(item)


class JobExperience: # Partially implemented at models.py:30
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

    def getJob(self):  # Partially implemented in engine/azure_engine.py, engine/data_loader.py
        return getJobFromID(self.jobID)

    def rebuildSkills(self):  # Not yet implemented elsewhere
        job = self.getJob()
        skills = {}
        for skillName in job.skills:
            skill = job.skills[skillName]
            if skill.level <= self.level:
                skills[skill.id] = skill
        self.skills = skills

    def levelUpJob(self):  # Implemented in engine/character.py:433
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
