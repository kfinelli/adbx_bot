from enum import Enum

from engine.azure_constants import POWER_LEVEL  # noqa: F401  (re-exported for callers)
from engine.azure_helpers import JOBS

# CharacterClass enum: member name = uppercase job id, value = display name.
# Generated automatically from job JSON files — no Python changes needed when
# adding a new class.  e.g. CharacterClass.KNIGHT.value == "Knight"
#
# Lives here (not in data_loader) because models.py must import CharacterClass
# to define Character.character_class, and data_loader imports engine.item
# which could create a circular dependency if CharacterClass were defined there.

CharacterClass = Enum(
    "CharacterClass",
    {job.id.upper(): job.displayName for job in JOBS.values()},
)

# CharacterClass member → Job (legacy Job object from azure_helpers).
# Used by CharacterManager.create_character and _do_level_up.
CREATION_RULES = {
    cls: JOBS[cls.name.lower()] for cls in CharacterClass
}
