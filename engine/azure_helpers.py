import json
import math

from engine.azure_constants import (
    _CLASSES_DIR,
    BASE_INVENTORY_SIZE,
    LEVEL_MULTIPLIER,
    POWER_LEVEL,
    Path,
    SkillType,
    Stat,
    StatPriority,
)
from engine.jobs import Job, Skill

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

# ---------------------------------------------------------------------------
# Weapon rank helpers
# ---------------------------------------------------------------------------

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
# Job Importer and Job Importer Helper Functions
# ---------------------------------------------------------------------------
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

JOBS = importJobs()

def getJobFromID(jobID):
    return JOBS[jobID]

