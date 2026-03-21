"""
Character management for the dungeon crawler engine.

NOTE — Stream A transition state
---------------------------------
This file is in an intermediate state after the Azure ruleset refactor.
The B/X-specific creation logic (equipment packages, AC from DEX, saving
throw tables, spell slots) has been removed.  The Azure creation flow
(stat rolling, HP from hit_die * POWER_LEVEL, base_save) is in place.

Stream B will update AbilityScores → AzureStats in models.py, at which
point roll_stat_block() will be updated to roll four Azure stats.
"""

from models import (
    AbilityScores,
    Character,
    CharacterClass,
    CharacterStatus,
    GameState,
    InventoryItem,
)
from azure_tables import (
    CREATION_RULES,
    POWER_LEVEL,
    get_stat_modifier,
)
from validation import validate_hp_value

from .dice import d, roll_sum
from .helpers import _err, _ok


class CharacterManager:
    """Manages character creation and mutations."""

    def create_character(
        self,
        state:           GameState,
        name:            str,
        character_class: CharacterClass,
        equipment_package: str = "",   # kept for API compat; no longer used
        owner_id:        str | None = None,
        ability_scores:  AbilityScores | None = None,
        prerolled_stats: dict | None = None,
    ):
        """
        Create a new level-1 character, add them to the session, and return
        the result.

        Azure creation flow:
          1. Roll or accept base stats (four Azure stats, stored × POWER_LEVEL).
          2. Look up job rules from CREATION_RULES.
          3. HP = hit_die * POWER_LEVEL (full HP at first level, like a JRPG).
          4. base_save = job.base_save * POWER_LEVEL.
          5. Start with empty inventory (items will be assigned separately).
          6. No spellbook on creation — spells are granted via skill progression.

        During Stream A the AbilityScores model still has the six B/X fields;
        they are used as placeholders.  Stream B will replace AbilityScores
        with AzureStats (four fields: physique, finesse, reason, savvy) and
        update roll_stat_block() accordingly.
        """
        from engine import _now

        if prerolled_stats is not None:
            ability_scores = AbilityScores(**prerolled_stats)
        if not name.strip():
            return _err(state, "Character name cannot be empty.")

        scores = ability_scores if ability_scores is not None else roll_stat_block()
        rules  = CREATION_RULES[character_class]

        # HP: full hit die at level 1, scaled by POWER_LEVEL
        hp_max = rules.hitDie * POWER_LEVEL

        # Saving throw: single scaled integer
        base_save = rules.baseSave * POWER_LEVEL

        character = Character(
            owner_id=owner_id,
            name=name,
            character_class=character_class,
            level=1,
            experience=0,
            ability_scores=scores,
            hp_max=hp_max,
            hp_current=hp_max,
            armor_class=9,          # placeholder; Stream B derives from gear
            movement_speed=120,     # placeholder; Stream B reads from job/gear
            saving_throws={"save": base_save},
            spellbook=None,
            inventory=[],
        )

        state.characters[character.character_id] = character

        if state.party is not None:
            state.party.member_ids.append(character.character_id)

        state.updated_at = _now()

        msg = (
            f"{name} the {character_class.value} joins the party! "
            f"HP: {hp_max}  Save: {base_save}"
        )
        return _ok(state, msg)

    def set_character_hp(
        self,
        state:        GameState,
        character_id,
        new_hp:       int,
    ):
        """Set a character's current HP."""
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        hp_result = validate_hp_value(new_hp, max_hp=char.hp_max)
        if not hp_result:
            return _err(state, hp_result.error)

        old = char.hp_current
        char.hp_current = hp_result.value
        if char.hp_current == 0:
            char.status = CharacterStatus.DEAD
        state.updated_at = _now()
        return _ok(state, f"{char.name} HP: {old} → {char.hp_current}/{char.hp_max}.")

    def set_character_status(
        self,
        state:        GameState,
        character_id,
        status:       CharacterStatus,
        notes:        str = "",
    ):
        """Set a character's status."""
        from engine import _now
        from validation import validate_description

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        notes_result = validate_description(
            notes, "Status notes", max_length=200, allow_empty=True
        )
        if not notes_result:
            return _err(state, notes_result.error)

        char.status = status
        char.status_notes = notes_result.value
        state.updated_at = _now()
        return _ok(
            state,
            f"{char.name} status → {status.value}. {notes_result.value}".strip(),
        )


def roll_stat_block() -> AbilityScores:
    """
    Roll base stats.

    Stream A: returns an AbilityScores with the six B/X fields populated
    using the Azure dice formula (2d(4*POWER_LEVEL) - 5*POWER_LEVEL per stat).
    Stream B will replace this with a proper AzureStats roll across four fields.
    """
    def _roll_azure_stat() -> int:
        die = 4 * POWER_LEVEL
        penalty = 5 * POWER_LEVEL
        return d(die) + d(die) - penalty

    # Map the four Azure stats onto the existing six-field AbilityScores model
    # using the most natural correspondence for now.
    # Stream B will replace this entirely.
    physique = _roll_azure_stat()
    finesse  = _roll_azure_stat()
    reason   = _roll_azure_stat()
    savvy    = _roll_azure_stat()

    return AbilityScores(
        strength=physique,
        dexterity=finesse,
        intelligence=reason,
        wisdom=savvy,
        constitution=0,
        charisma=0,
    )
