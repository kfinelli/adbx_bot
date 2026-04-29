"""
NPC management for the dungeon crawler engine.
"""

import copy
from uuid import UUID, uuid4

from models import NPC, EncounterEntry, GameState, NPCGroup, NPCMovementLogic
from validation import validate_hp_value, validate_non_empty_string

from .helpers import _err, _find_npc_in_roster, _find_npcgroup_with_npc, _now, _ok
from .strings import fmt_string, get_string


class NPCManager:
    """Manages NPCs in the game via the NPC roster."""

    def add_npc_group(self, state: GameState, group: NPCGroup):
        """Add an NPC group to the roster. The group should already be validated before calling."""
        state.npc_roster.add_group(group)
        state.updated_at = _now()
        npc_count = len(group.npcs)
        group_name = group.name or f"Group {group.group_id}"
        return _ok(state, fmt_string("npc.group_added", npc_count=npc_count, group_name=group_name))

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
        Add a single NPC to a room. If an NPC group already exists in the room,
        adds the NPC to the first existing group. Otherwise creates a new group.

        Args:
            state: Current game state
            npc: The NPC to add
            room_id: Room to place the NPC in (defaults to current room)
            group_name: Optional name for the NPC group
            movement_logic: How the NPC group moves
            possible_rooms: List of room IDs where this group may be found
        """
        target_room = room_id if room_id is not None else state.current_room_id

        # Check if there's an existing NPC group in the target room
        existing_group = state.npc_roster.get_group_in_room(target_room)

        if existing_group:
            # Add NPC to the existing group
            existing_group.npcs.append(npc)
            state.updated_at = _now()
            return _ok(state, fmt_string("npc.appears", name=npc.name))
        else:
            # Create a new group for this NPC
            group = NPCGroup(
                name=group_name,
                npcs=[npc],
                movement_logic=movement_logic,
                current_room_id=target_room,
                possible_rooms=possible_rooms or [],
            )
            state.npc_roster.add_group(group)
            state.updated_at = _now()
            return _ok(state, fmt_string("npc.appears", name=npc.name))

    def move_npc_group_to_room(self, state: GameState, group_id, room_id):
        """Move an NPC group to a new room."""
        success = state.npc_roster.move_group_to_room(group_id, room_id)
        if not success:
            return _err(state, fmt_string("npc.errors.move_failed", group_id=group_id, room_id=room_id))
        state.updated_at = _now()
        return _ok(state, fmt_string("npc.group_moved", room_id=room_id))

    def set_npc_hp(
        self,
        state:  GameState,
        npc_id,
        new_hp: int,
    ):
        """Set an NPC's current HP. Searches the entire roster."""
        npc = _find_npc_in_roster(state, npc_id)
        if npc is None:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))

        # Validate HP value
        hp_result = validate_hp_value(new_hp)
        if not hp_result:
            return _err(state, hp_result.error)

        old = npc.hp_current
        npc.hp_current = hp_result.value
        if npc.hp_current == 0:
            npc.status = "dead"
        state.updated_at = _now()
        return _ok(state, fmt_string("npc.hp_updated", name=npc.name, old=old, hp_current=npc.hp_current, hp_max=npc.hp_max))

    def set_npc_status(
        self,
        state:  GameState,
        npc_id,
        status: str,
    ):
        """Set an NPC's status. Searches the entire roster."""
        npc = _find_npc_in_roster(state, npc_id)
        if npc is None:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))

        # Validate status string
        status_result = validate_non_empty_string(status, "NPC status", max_length=50)
        if not status_result:
            return _err(state, status_result.error)

        npc.status = status_result.value
        state.updated_at = _now()
        return _ok(state, fmt_string("npc.status_updated", name=npc.name, status=status_result.value))

    def set_npc_visibility(
        self,
        state:  GameState,
        npc_id,
        hidden: bool,
    ):
        """Show or hide an NPC from player views."""
        npc = _find_npc_in_roster(state, npc_id)
        if npc is None:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))
        npc.hidden = hidden
        state.updated_at = _now()
        label = "hidden" if hidden else "visible"
        return _ok(state, f"{npc.name} is now {label}.")

    def remove_npc_group(self, state: GameState, group_id):
        """Remove an NPC group from the roster."""
        success = state.npc_roster.remove_group(group_id)
        if not success:
            return _err(state, fmt_string("npc.errors.group_not_found", group_id=group_id))
        state.updated_at = _now()
        return _ok(state, get_string("npc.group_removed"))

    def remove_npc(self, state: GameState, npc_id):
        """Remove an NPC from its group (and the roster)."""
        group = _find_npcgroup_with_npc(state, npc_id)
        if group is None:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))
        success = group.remove_npc(npc_id)
        if not success:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))
        state.updated_at = _now()
        return _ok(state, "NPC removed.")

    def update_npc(
        self,
        state:        GameState,
        npc_id,
        name:         str,
        description:  str,
        hp_max:       int,
        hp_current:   int,
        defense:      int,
        notes:        str = "",
        hit_dice:     int = 1,
        resistance:   int = 0,
        weapon_range: int = 0,
        damage_dice:  str = "1d6",
    ):
        """Update an NPC's attributes. Searches the entire roster."""
        npc = _find_npc_in_roster(state, npc_id)
        if npc is None:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))

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

        # Validate DEF
        from validation import validate_bounded_int
        def_result = validate_bounded_int(defense, "Defense", min_value=0)
        if not def_result:
            return _err(state, def_result.error)

        # Validate description and notes
        from validation import validate_description
        desc_result = validate_description(description, "NPC description", max_length=500, allow_empty=True)
        if not desc_result:
            return _err(state, desc_result.error)

        notes_result = validate_description(notes, "NPC notes", max_length=500, allow_empty=True)
        if not notes_result:
            return _err(state, notes_result.error)

        npc.name         = name_result.value
        npc.description  = desc_result.value
        npc.hp_max       = hp_max_result.value
        npc.hp_current   = hp_current_result.value
        npc.defense      = def_result.value
        npc.notes        = notes_result.value
        npc.hit_dice     = max(1, int(hit_dice))
        npc.resistance   = max(0, int(resistance))
        npc.weapon_range = max(0, int(weapon_range))
        if damage_dice and damage_dice.strip():
            npc.damage_dice = damage_dice.strip()
        state.updated_at = _now()
        return _ok(state, fmt_string("npc.updated", name=npc.name))

    def copy_npc(self, state: GameState, npc_id, room_id=None):
        """Deep-copy an NPC and place the copy in the same room."""
        npc = _find_npc_in_roster(state, npc_id)
        if npc is None:
            return _err(state, fmt_string("npc.errors.not_found", npc_id=npc_id))

        group = _find_npcgroup_with_npc(state, npc_id)
        target_room = room_id if room_id is not None else (
            group.current_room_id if group else state.current_room_id
        )

        def _modify_end(s):
            '''helper to make new NPC name'''
            if not s or not s[-1].isalpha():
                return s + "A"

            # Simple increment logic
            last = s[-1]
            if last == 'Z':
                return s[:-1] + 'AA' # Example Z wrap
            if last == 'z':
                return s[:-1] + 'aa'

            return s[:-1] + chr(ord(last) + 1)

        new_npc = copy.deepcopy(npc)
        new_npc.npc_id = uuid4()
        new_npc.active_conditions = []
        new_npc.hp_current = new_npc.hp_max
        raw_name = _modify_end(npc.name)
        new_npc.name = raw_name[:50]

        return self.add_npc_to_room(state, new_npc, room_id=target_room)

    def add_npc_to_group(self, state: GameState, group_id: UUID, npc: NPC):
        """Add an NPC directly to a specific existing group."""
        group = state.npc_roster.get_group(group_id)
        if group is None:
            return _err(state, fmt_string("npc.errors.group_not_found", group_id=group_id))
        group.npcs.append(npc)
        state.updated_at = _now()
        return _ok(state, fmt_string("npc.appears", name=npc.name))

    def update_group(
        self,
        state: GameState,
        group_id: UUID,
        name: str | None,
        movement_logic: NPCMovementLogic,
        current_room_id: UUID | None,
        possible_rooms: list[UUID],
    ):
        """Update group-level properties."""
        group = state.npc_roster.get_group(group_id)
        if group is None:
            return _err(state, fmt_string("npc.errors.group_not_found", group_id=group_id))
        group.name = name or None
        group.movement_logic = movement_logic
        group.current_room_id = current_room_id
        group.possible_rooms = possible_rooms
        state.updated_at = _now()
        return _ok(state, "Group updated.")

    def add_encounter_entry(self, state: GameState, npc_group_template: NPCGroup, weight: int):
        """Append a new entry to the dungeon's random encounter roster."""
        if state.dungeon is None:
            return _err(state, "No dungeon loaded.")
        weight = max(1, int(weight))
        entry = EncounterEntry(npc_group=npc_group_template, weight=weight)
        state.dungeon.random_encounter_roster.append(entry)
        state.updated_at = _now()
        group_name = npc_group_template.name or "Unnamed group"
        return _ok(state, f"Added '{group_name}' to encounter roster.")

    def remove_encounter_entry(self, state: GameState, group_id: UUID):
        """Remove the encounter entry whose template group_id matches."""
        if state.dungeon is None:
            return _err(state, "No dungeon loaded.")
        roster = state.dungeon.random_encounter_roster
        for i, entry in enumerate(roster):
            if entry.npc_group.group_id == group_id:
                del roster[i]
                state.updated_at = _now()
                return _ok(state, "Encounter entry removed.")
        return _err(state, "Encounter entry not found.")

    def update_encounter_entry_weight(self, state: GameState, group_id: UUID, weight: int):
        """Update the weight of an encounter entry identified by its template group_id."""
        if state.dungeon is None:
            return _err(state, "No dungeon loaded.")
        for entry in state.dungeon.random_encounter_roster:
            if entry.npc_group.group_id == group_id:
                entry.weight = max(1, int(weight))
                state.updated_at = _now()
                return _ok(state, "Encounter weight updated.")
        return _err(state, "Encounter entry not found.")

    def promote_group_to_encounter(self, state: GameState, group_id: UUID, weight: int):
        """Deep-copy a live group and add it as an encounter roster template."""
        group = state.npc_roster.get_group(group_id)
        if group is None:
            return _err(state, fmt_string("npc.errors.group_not_found", group_id=group_id))
        template = copy.deepcopy(group)
        template.group_id = uuid4()
        for npc in template.npcs:
            npc.npc_id = uuid4()
            npc.active_conditions = []
        template.current_room_id = None
        return self.add_encounter_entry(state, template, weight)

    def update_encounter_npc(
        self,
        state: GameState,
        encounter_group_id: UUID,
        npc_id: UUID,
        name: str,
        description: str,
        hp_max: int,
        defense: int,
        notes: str = "",
        hit_dice: int = 1,
        resistance: int = 0,
        weapon_range: int = 0,
        damage_dice: str = "1d6",
    ):
        """Update an NPC inside an encounter roster template group."""
        if state.dungeon is None:
            return _err(state, "No dungeon loaded.")
        for entry in state.dungeon.random_encounter_roster:
            if entry.npc_group.group_id == encounter_group_id:
                for npc in entry.npc_group.npcs:
                    if npc.npc_id == npc_id:
                        name_result = validate_non_empty_string(name, "NPC name", max_length=50)
                        if not name_result:
                            return _err(state, name_result.error)
                        hp_result = validate_hp_value(hp_max)
                        if not hp_result:
                            return _err(state, hp_result.error)
                        npc.name = name_result.value
                        npc.description = description[:500]
                        npc.hp_max = hp_result.value
                        npc.hp_current = hp_result.value
                        npc.defense = max(0, int(defense))
                        npc.notes = notes[:500]
                        npc.hit_dice = max(1, int(hit_dice))
                        npc.resistance = max(0, int(resistance))
                        npc.weapon_range = max(0, int(weapon_range))
                        if damage_dice and damage_dice.strip():
                            npc.damage_dice = damage_dice.strip()
                        state.updated_at = _now()
                        return _ok(state, f"{npc.name} updated.")
                return _err(state, "NPC not found in encounter group.")
        return _err(state, "Encounter entry not found.")

    def remove_encounter_npc(self, state: GameState, encounter_group_id: UUID, npc_id: UUID):
        """Remove an NPC from an encounter roster template group."""
        if state.dungeon is None:
            return _err(state, "No dungeon loaded.")
        for entry in state.dungeon.random_encounter_roster:
            if entry.npc_group.group_id == encounter_group_id:
                success = entry.npc_group.remove_npc(npc_id)
                if not success:
                    return _err(state, "NPC not found in encounter group.")
                state.updated_at = _now()
                return _ok(state, "NPC removed from encounter template.")
        return _err(state, "Encounter entry not found.")

    def add_npc_to_encounter_group(self, state: GameState, encounter_group_id: UUID, npc: NPC):
        """Add an NPC to an encounter roster template group."""
        if state.dungeon is None:
            return _err(state, "No dungeon loaded.")
        for entry in state.dungeon.random_encounter_roster:
            if entry.npc_group.group_id == encounter_group_id:
                entry.npc_group.npcs.append(npc)
                state.updated_at = _now()
                return _ok(state, fmt_string("npc.appears", name=npc.name))
        return _err(state, "Encounter entry not found.")
