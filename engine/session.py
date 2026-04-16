"""
Session management for the dungeon crawler engine.
"""

from models import GameState, SessionMode

from .helpers import _err, _now, _ok
from .strings import fmt_string, get_string


class SessionManager:
    """Manages session lifecycle."""

    def start_session(self, state: GameState):
        """
        DM command: transition from PRE_START to EXPLORATION.
        Opens the first dungeon turn.
        """
        if state.mode != SessionMode.PRE_START:
            return _err(state, get_string("session.errors.already_started"))
        if not state.characters:
            return _err(state, get_string("session.errors.no_characters"))
        state.mode = SessionMode.EXPLORATION
        state.session_active = True
        if state.current_room_id and state.dungeon and state.current_room_id in state.dungeon.rooms:
            state.dungeon.rooms[state.current_room_id].visited = True
        state.updated_at = _now()

        # Open first turn
        from .core import TurnManager
        tm = TurnManager()
        tm.open_turn(state)

        return _ok(state, get_string("session.started"))

    def hold_session(self, state: GameState):
        """Put the session on hold. No player turns or DM commands accepted until resumed."""
        if not state.session_active:
            return _err(state, get_string("session.errors.already_on_hold"))
        state.session_active = False
        state.updated_at = _now()
        return _ok(state, get_string("session.on_hold"))

    def resume_session(self, state: GameState):
        """Resume a session that was put on hold."""
        if state.session_active:
            return _err(state, get_string("session.errors.not_on_hold"))
        state.session_active = True
        state.updated_at = _now()
        return _ok(state, get_string("session.resumed"))

    def enter_rounds(self, state: GameState):
        """
        Switch to combat rounds mode.
        Saves the current exploration turn number, resets the round counter
        to 1, and initialises the battlefield with all active characters and
        room NPCs.
        """
        if state.mode == SessionMode.ROUNDS:
            return _err(state, get_string("session.errors.already_in_rounds"))
        state.rounds_started_at_turn = state.turn_number
        state.mode = SessionMode.ROUNDS
        state.turn_number = 1
        if state.current_turn:
            state.current_turn.mode = SessionMode.ROUNDS
            state.current_turn.turn_number = 1

        # Initialise battlefield — lazy import to avoid circular dependency
        # engine/__init__ → session → combat → engine/__init__
        from .combat import initialize_battlefield
        state.battlefield = initialize_battlefield(state)

        state.updated_at = _now()
        return _ok(state, get_string("session.entering_rounds"))

    def exit_rounds(self, state: GameState):
        """
        Return to exploration mode.
        Restores the exploration turn counter, advancing by 1 to account
        for the turn consumed by combat (standard B/X ruling).
        Clears the battlefield.
        """
        if state.mode == SessionMode.EXPLORATION:
            return _err(state, get_string("session.errors.already_in_exploration"))
        state.mode = SessionMode.EXPLORATION
        resumed_at = (state.rounds_started_at_turn or state.turn_number) + 1
        state.turn_number = resumed_at
        state.rounds_started_at_turn = None
        state.battlefield = None
        # Expire any conditions scoped to rounds (duration_rounds is not None).
        # Permanent conditions (duration_rounds=None) survive into exploration.
        for char in state.characters.values():
            char.active_conditions = [
                c for c in char.active_conditions if c.duration_rounds is None
            ]
        for group in state.npc_roster.groups.values():
            for npc in group.npcs:
                npc.active_conditions = [
                    c for c in npc.active_conditions if c.duration_rounds is None
                ]
        if state.current_turn:
            state.current_turn.mode = SessionMode.EXPLORATION
            state.current_turn.turn_number = resumed_at
        # Restore encounter-period spell charges for all characters.
        from engine.azure_constants import RechargePeriod
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import ChargeWeapon, UtilitySpell
        for char in state.characters.values():
            for inv_item in char.inventory:
                if inv_item.charges is None:
                    continue
                defn = ITEM_REGISTRY.get(inv_item.item_id)
                if defn is None:
                    continue
                if not isinstance(defn, (ChargeWeapon, UtilitySpell)):
                    continue
                if getattr(defn, "rechargePeriod", None) != RechargePeriod.ENCOUNTER:
                    continue
                if defn.maxCharges < 0:
                    continue
                inv_item.charges = defn.maxCharges
        state.updated_at = _now()
        return _ok(state, get_string("session.returning_to_exploration"))

    def import_dungeon(self, state: GameState, dungeon, npc_roster=None):
        """
        Load a pre-authored dungeon and optionally an NPC roster into the session.

        Only permitted in PRE_START — the dungeon must be set before the
        session begins so players arrive into a known map. Replaces any
        previously loaded dungeon wholesale.

        If the dungeon has an entrance_id, the current room is set to that room
        so the DM can immediately see it in the web UI and status block. The
        room is NOT marked visited — that happens when the party actually
        enters via /embark + /dm_setroom or move_party_to_room.

        Args:
            state: Current game state
            dungeon: The Dungeon object to load
            npc_roster: Optional NPCRoster to load (replaces existing roster if provided)
        """
        if state.mode != SessionMode.PRE_START:
            return _err(state, get_string("session.errors.dungeon_import_timing"))
        state.dungeon = dungeon
        # Replace the NPC roster if provided
        if npc_roster is not None:
            state.npc_roster = npc_roster
        # Point current_room_id at the entrance so the web UI has something
        # to show; visited is marked True when the session starts.
        if dungeon.entrance_id and dungeon.entrance_id in dungeon.rooms:
            state.current_room_id = dungeon.entrance_id
        state.updated_at = _now()
        room_count = len(dungeon.rooms)
        return _ok(state, fmt_string("session.dungeon_loaded", name=dungeon.name, room_count=room_count))
