"""
Room management for the dungeon crawler engine.
"""

from models import (
    DoorState,
    Dungeon,
    Exit,
    GameState,
    Room,
)
from validation import (
    validate_description,
    validate_door_state,
    validate_non_empty_string,
)

from .helpers import _err, _now, _ok, _resolve_room
from .strings import fmt_string, get_string


class RoomManager:
    """Manages rooms, features, and exits."""

    def register_room(self, state: GameState, room: Room):
        """
        Add a room to the dungeon graph without moving the party into it.
        Used by the web UI when authoring rooms before or during a session.
        To also move the party in, call move_party_to_room() afterwards.
        """
        if state.dungeon is None:
            state.dungeon = Dungeon(name="The Dungeon")

        # Validate room name
        if not room.name or not room.name.strip():
            return _err(state, get_string("room.errors.name_empty"))

        # Validate room description length
        if room.description and len(room.description) > 1000:
            return _err(state, get_string("room.errors.description_too_long"))

        # Validate room notes length
        if room.notes and len(room.notes) > 2000:
            return _err(state, get_string("room.errors.notes_too_long"))

        state.dungeon.rooms[room.room_id] = room
        state.updated_at = _now()
        return _ok(state, fmt_string("room.added", name=room.name))

    def set_room(self, state: GameState, room: Room):
        """
        DM creates a new room on the fly and immediately moves the party in.
        Adds it to the dungeon graph and sets it as current.
        Used by the /dm_setroom slash command (no room_id) path.
        For web UI room creation, use register_room() instead.
        """
        result = self.register_room(state, room)
        if not result.ok:
            return result

        state.current_room_id = room.room_id
        room.visited = True
        state.updated_at = _now()
        return _ok(state, fmt_string("room.entered", name=room.name))

    def move_party_to_room(self, state: GameState, room_id):
        """
        Move the party into an already-authored room in the dungeon graph.

        - Looks up the room by ID; fails if not found.
        - Marks the room visited.
        - Does NOT modify the room's features, exits, or any other authored data.
        - NPCs in the roster remain in their rooms; use npc_roster for persistent NPCs.
        """
        if state.dungeon is None:
            return _err(state, get_string("room.errors.no_dungeon"))
        room = state.dungeon.rooms.get(room_id)
        if room is None:
            return _err(state, fmt_string("room.errors.not_found", room_id=room_id))
        state.current_room_id = room_id
        room.visited = True
        state.updated_at = _now()
        return _ok(state, fmt_string("room.entered", name=room.name))

    def update_room(
        self,
        state:       GameState,
        room_id,
        name:        str,
        description: str,
        notes:       str = "",
    ):
        """Edit the name, description, and DM notes of an existing room."""
        if not name.strip():
            return _err(state, get_string("room.errors.name_empty"))
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, fmt_string("room.errors.not_found", room_id=room_id))
        room.name        = name.strip()
        room.description = description
        room.notes       = notes
        state.updated_at = _now()
        return _ok(state, fmt_string("room.updated", name=room.name))

    def delete_feature(
        self,
        state:      GameState,
        feature_id,
        room_id = None,
    ):
        """Delete a feature from a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        before = len(room.features)
        room.features = [f for f in room.features if f.feature_id != feature_id]
        if len(room.features) == before:
            return _err(state, fmt_string("room.feature.not_found", feature_id=feature_id))
        state.updated_at = _now()
        return _ok(state, get_string("room.feature.deleted"))

    def update_feature(
        self,
        state:       GameState,
        feature_id,
        name:        str,
        description: str,
        state_str:   str,
        notes:       str = "",
        room_id = None,
    ):
        """Update a feature in a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        feat = next((f for f in room.features if f.feature_id == feature_id), None)
        if feat is None:
            return _err(state, fmt_string("room.feature.not_found", feature_id=feature_id))
        if not name.strip():
            return _err(state, get_string("room.feature.errors.name_empty"))
        feat.name        = name.strip()
        feat.description = description
        feat.state       = state_str
        feat.notes       = notes
        state.updated_at = _now()
        return _ok(state, fmt_string("room.feature.updated", name=feat.name))

    def delete_exit(
        self,
        state:   GameState,
        exit_id,
        room_id = None,
    ):
        """Delete an exit from a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        before = len(room.exits)
        room.exits = [e for e in room.exits if e.exit_id != exit_id]
        if len(room.exits) == before:
            return _err(state, fmt_string("room.exit.not_found", exit_id=exit_id))
        state.updated_at = _now()
        return _ok(state, "Exit deleted.")

    def update_exit(
        self,
        state:          GameState,
        exit_id,
        label:          str,
        description:    str,
        door_state,
        destination_id = None,
        notes:          str = "",
        auto_move:      bool = False,
        room_id = None,
    ):
        """Update an exit in a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        ex = next((e for e in room.exits if e.exit_id == exit_id), None)
        if ex is None:
            return _err(state, fmt_string("room.exit.not_found", exit_id=exit_id))
        if not label.strip():
            return _err(state, get_string("room.exit.errors.label_empty"))
        ex.label          = label.strip()
        ex.description    = description
        ex.door_state     = door_state
        ex.destination_id = destination_id
        ex.notes          = notes
        ex.auto_move      = auto_move
        state.updated_at  = _now()
        return _ok(state, fmt_string("room.exit.updated", label=ex.label))

    def set_feature_state(
        self,
        state:      GameState,
        feature_id,
        new_state:  str,
        room_id = None,
    ):
        """Update the state string of a room feature."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        # Validate new state
        state_result = validate_non_empty_string(new_state, "Feature state", max_length=100)
        if not state_result:
            return _err(state, state_result.error)

        for feat in room.features:
            if feat.feature_id == feature_id:
                feat.state = state_result.value
                state.updated_at = _now()
                return _ok(state, fmt_string("room.feature.state_set", name=feat.name, state=state_result.value))
        return _err(state, fmt_string("room.feature.not_found", feature_id=feature_id))

    def set_exit_state(
        self,
        state,
        exit_id,
        new_state,
        room_id = None,
    ):
        """Set the door state of an exit."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        # Validate door state
        state_result = validate_door_state(new_state)
        if not state_result:
            return _err(state, state_result.error)

        for ex in room.exits:
            if ex.exit_id == exit_id:
                ex.door_state = state_result.value
                state.updated_at = _now()
                return _ok(state, f"Exit '{ex.label}' → {state_result.value.value}.")
        return _err(state, f"Exit {exit_id} not found.")

    def add_exit(
        self,
        state:       GameState,
        label:       str,
        description: str,
        door_state=DoorState.OPEN,
        notes:       str = "",
        room_id = None,
    ):
        """DM adds a new exit."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        # Validate label
        label_result = validate_non_empty_string(label, "Exit label", max_length=50)
        if not label_result:
            return _err(state, label_result.error)

        # Validate description
        desc_result = validate_description(description, "Exit description", max_length=200)
        if not desc_result:
            return _err(state, desc_result.error)

        # Validate door state
        door_result = validate_door_state(door_state)
        if not door_result:
            return _err(state, door_result.error)

        # Validate notes
        notes_result = validate_description(notes, "Exit notes", max_length=500, allow_empty=True)
        if not notes_result:
            return _err(state, notes_result.error)

        exit_ = Exit(
            label=label_result.value,
            description=desc_result.value,
            door_state=door_result.value,
            notes=notes_result.value,
        )
        room.exits.append(exit_)
        n = len(room.exits)
        state.updated_at = _now()
        return _ok(state, f"Exit {n} added: {label_result.value}.")
