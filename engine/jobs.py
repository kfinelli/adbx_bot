from engine.azure_constants import Stat, StatPriority

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

class Skill:
    def __init__(self, id, name, source, level, type, description=""):
        self.id = id
        self.name = name
        self.source = source
        self.level = level
        self.type = type
        self.description = description
        self.dm_notes = ""

