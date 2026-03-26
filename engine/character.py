"""
Character management for the dungeon crawler engine.
"""
from uuid import UUID, uuid4

from engine.azure_engine import CREATION_RULES, POWER_LEVEL
from engine.azure_constants import ItemSlot
from models import (
    AzureStats,
    Character,
    CharacterClass,
    CharacterStatus,
    GameState,
)
from validation import validate_hp_value

from .dice import roll_stat_block
from .helpers import _err, _ok


class CharacterManager:
    """Manages character creation and mutations."""

    def create_character(
        self,
        state:             GameState,
        name:              str,
        character_class:   CharacterClass,
        equipment_package: str = "",        # unused in Azure; kept for API compat
        owner_id:          str | None = None,
        ability_scores:    AzureStats | None = None,
        prerolled_stats:   dict | None = None,
    ):
        """
        Create a new level-1 character and add them to the session.

        Azure creation flow:
          1. Roll or accept base stats (four Azure stats, scaled by POWER_LEVEL).
          2. Look up job rules from CREATION_RULES.
          3. HP = hit_die * POWER_LEVEL (full HP at level 1).
          4. base_save = job.baseSave * POWER_LEVEL.
          5. Empty inventory — items are assigned separately.
          6. No spellbook — spells come from skill progression.

        prerolled_stats, if provided, must be a dict with keys
        physique / finesse / reason / savvy (as returned by roll_stats()).
        """
        from engine import _now

        if prerolled_stats is not None:
            ability_scores = AzureStats(
                physique=prerolled_stats.get("physique", 0),
                finesse=prerolled_stats.get("finesse",  0),
                reason=prerolled_stats.get("reason",   0),
                savvy=prerolled_stats.get("savvy",     0),
            )

        if not name.strip():
            return _err(state, "Character name cannot be empty.")

        scores = ability_scores if ability_scores is not None else roll_stat_block()
        rules  = CREATION_RULES[character_class]

        hp_max    = rules.hitDie   * POWER_LEVEL
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
            movement_speed=120,
            saving_throws={"save": base_save},
            gold=100, #Placeholder value to test arrive shopping
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

    def set_character_hp(self, state: GameState, character_id, new_hp: int):
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
        self, state: GameState, character_id, status: CharacterStatus, notes: str = ""
    ):
        from engine import _now
        from validation import validate_description
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        notes_result = validate_description(notes, "Status notes", max_length=200, allow_empty=True)
        if not notes_result:
            return _err(state, notes_result.error)
        char.status = status
        char.status_notes = notes_result.value
        state.updated_at = _now()
        return _ok(state, f"{char.name} status → {status.value}. {notes_result.value}".strip())

    def equip_inventory_item(self, state: GameState, character_id: UUID, item_id: UUID, item_slot: ItemSlot):
        """ Check for item with item_id UUID, if it belongs to the character's
        inventory, equip it to the designated slot. If no slot is specified,
        equip it to the appropriate slot, trying to find an empty slot if
        possible. """
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        # check if item_id is in character inventory

        # check if item slot is available

        # unequip old item

        # equip the item
