"""
NPC management for the dungeon crawler engine.
"""

from models import GameState, NPC
from validation import validate_hp_value, validate_non_empty_string

from .helpers import _ok, _err, _find_npc


class NPCManager:
    """Manages NPCs in the game."""

    def add_npc(self, state: GameState, npc: NPC):
        """Add an NPC to the current room. NPC should already be validated before calling."""
        state.npcs.append(npc)
        state.updated_at = _now()
        return _ok(state, f"{npc.name} appears.")

    def set_npc_hp(
        self,
        state:  GameState,
        npc_id,
        new_hp: int,
    ):
        """Set an NPC's current HP."""
        npc = _find_npc(state, npc_id)
        if npc is None:
            return _err(state, f"NPC {npc_id} not found.")

        # Validate HP value
        hp_result = validate_hp_value(new_hp)
        if not hp_result:
            return _err(state, hp_result.error)

        old = npc.hp_current
        npc.hp_current = hp_result.value
        if npc.hp_current == 0:
            npc.status = "dead"
        state.updated_at = _now()
        return _ok(state, f"{npc.name} HP: {old} → {npc.hp_current}/{npc.hp_max}.")

    def set_npc_status(
        self,
        state:  GameState,
        npc_id,
        status: str,
    ):
        """Set an NPC's status."""
        npc = _find_npc(state, npc_id)
        if npc is None:
            return _err(state, f"NPC {npc_id} not found.")

        # Validate status string
        status_result = validate_non_empty_string(status, "NPC status", max_length=50)
        if not status_result:
            return _err(state, status_result.error)

        npc.status = status_result.value
        state.updated_at = _now()
        return _ok(state, f"{npc.name} status → {status_result.value}.")

    def remove_npc(self, state: GameState, npc_id):
        """Remove an NPC from the current room."""
        npc = _find_npc(state, npc_id)
        if npc is None:
            return _err(state, f"NPC {npc_id} not found.")
        state.npcs = [n for n in state.npcs if n.npc_id != npc_id]
        state.updated_at = _now()
        return _ok(state, f"{npc.name} removed from room.")

    def update_npc(
        self,
        state:       GameState,
        npc_id,
        name:        str,
        description: str,
        hp_max:      int,
        hp_current:  int,
        armor_class: int,
        notes:       str = "",
    ):
        """Update an NPC's attributes."""
        npc = _find_npc(state, npc_id)
        if npc is None:
            return _err(state, f"NPC {npc_id} not found.")

        # Validate name
        name_result = validate_non_empty_string(name, "NPC name", max_length=50)
        if not name_result:
            return _err(state, name_result.error)

        # Validate HP values
        hp_max_result = validate_hp_value(hp_max)
        if not hp_max_result:
            return _err(state, hp_max_result.error)

        hp_current_result = validate_hp_value(hp_current, max_hp=hp_max_result.value)
        if not hp_current_result:
            return _err(state, hp_current_result.error)

        # Validate AC
        from validation import validate_positive_int
        ac_result = validate_positive_int(armor_class, "Armor class", min_value=1, max_value=20)
        if not ac_result:
            return _err(state, ac_result.error)

        # Validate description and notes
        from validation import validate_description
        desc_result = validate_description(description, "NPC description", max_length=500, allow_empty=True)
        if not desc_result:
            return _err(state, desc_result.error)

        notes_result = validate_description(notes, "NPC notes", max_length=500, allow_empty=True)
        if not notes_result:
            return _err(state, notes_result.error)

        npc.name        = name_result.value
        npc.description = desc_result.value
        npc.hp_max      = hp_max_result.value
        npc.hp_current  = hp_current_result.value
        npc.armor_class = ac_result.value
        npc.notes       = notes_result.value
        state.updated_at = _now()
        return _ok(state, f"NPC updated: {npc.name}.")


def _now():
    """Get current UTC datetime."""
    from datetime import UTC, datetime
    return datetime.now(UTC)
