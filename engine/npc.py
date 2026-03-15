"""
NPC management for the dungeon crawler engine.
"""

from models import NPC, NPCGroup, GameState, NPCMovementLogic
from validation import validate_hp_value, validate_non_empty_string

from .helpers import _err, _find_npc_in_roster, _ok


class NPCManager:
    """Manages NPCs in the game via the NPC roster."""

    def add_npc_group(self, state: GameState, group: NPCGroup):
        """Add an NPC group to the roster. The group should already be validated before calling."""
        state.npc_roster.add_group(group)
        state.updated_at = _now()
        npc_count = len(group.npcs)
        group_name = group.name or f"Group {group.group_id}"
        return _ok(state, f"{npc_count} NPC(s) added: {group_name}.")

    def add_npc_to_room(
        self,
        state: GameState,
        npc: NPC,
        room_id=None,
        group_name: str | None = None,
        movement_logic: NPCMovementLogic = NPCMovementLogic.STATIONARY,
        possible_rooms: list | None = None,
    ):
        """
        Add a single NPC to a room by creating a new group for it.
        
        Args:
            state: Current game state
            npc: The NPC to add
            room_id: Room to place the NPC in (defaults to current room)
            group_name: Optional name for the NPC group
            movement_logic: How the NPC group moves
            possible_rooms: List of room IDs where this group may be found
        """
        target_room = room_id if room_id is not None else state.current_room_id
        if target_room is None:
            return _err(state, "No room specified and no current room set.")
        
        group = NPCGroup(
            name=group_name,
            npcs=[npc],
            movement_logic=movement_logic,
            current_room_id=target_room,
            possible_rooms=possible_rooms or [],
        )
        state.npc_roster.add_group(group)
        state.updated_at = _now()
        return _ok(state, f"{npc.name} appears.")

    def move_npc_group_to_room(self, state: GameState, group_id, room_id):
        """Move an NPC group to a new room."""
        success = state.npc_roster.move_group_to_room(group_id, room_id)
        if not success:
            return _err(state, f"Could not move NPC group {group_id} to room {room_id}.")
        state.updated_at = _now()
        group = state.npc_roster.get_group(group_id)
        return _ok(state, f"NPC group moved to room {room_id}.")

    def set_npc_hp(
        self,
        state:  GameState,
        npc_id,
        new_hp: int,
    ):
        """Set an NPC's current HP. Searches the entire roster."""
        npc = _find_npc_in_roster(state, npc_id)
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
        """Set an NPC's status. Searches the entire roster."""
        npc = _find_npc_in_roster(state, npc_id)
        if npc is None:
            return _err(state, f"NPC {npc_id} not found.")

        # Validate status string
        status_result = validate_non_empty_string(status, "NPC status", max_length=50)
        if not status_result:
            return _err(state, status_result.error)

        npc.status = status_result.value
        state.updated_at = _now()
        return _ok(state, f"{npc.name} status → {status_result.value}.")

    def remove_npc_group(self, state: GameState, group_id):
        """Remove an NPC group from the roster."""
        success = state.npc_roster.remove_group(group_id)
        if not success:
            return _err(state, f"NPC group {group_id} not found.")
        state.updated_at = _now()
        return _ok(state, f"NPC group removed.")

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
        """Update an NPC's attributes. Searches the entire roster."""
        npc = _find_npc_in_roster(state, npc_id)
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
