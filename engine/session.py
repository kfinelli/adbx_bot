"""
Session management for the dungeon crawler engine.
"""

from models import GameState, SessionMode

from .helpers import _err, _ok


class SessionManager:
    """Manages session lifecycle."""

    def start_session(self, state: GameState):
        """
        DM command: transition from PRE_START to EXPLORATION.
        Opens the first dungeon turn.
        """
        if state.mode != SessionMode.PRE_START:
            return _err(state, "Session is already started.")
        if not state.characters:
            return _err(state, "No characters have arrived yet.")
        state.mode = SessionMode.EXPLORATION
        state.session_active = True
        state.updated_at = _now()

        # Open first turn
        from .core import TurnManager
        tm = TurnManager()
        tm.open_turn(state)

        return _ok(state, "Session started. The adventure begins!")

    def hold_session(self, state: GameState):
        """Put the session on hold. No player turns or DM commands accepted until resumed."""
        if not state.session_active:
            return _err(state, "Session is already on hold.")
        state.session_active = False
        state.updated_at = _now()
        return _ok(state, "Session is now on hold.")

    def resume_session(self, state: GameState):
        """Resume a session that was put on hold."""
        if state.session_active:
            return _err(state, "Session is not on hold.")
        state.session_active = True
        state.updated_at = _now()
        return _ok(state, "Session resumed.")

    def enter_rounds(self, state: GameState):
        """
        Switch to combat rounds mode.
        Saves the current exploration turn number and resets the counter to 1
        so rounds are counted from Round 1.
        """
        if state.mode == SessionMode.ROUNDS:
            return _err(state, "Already in rounds.")
        state.rounds_started_at_turn = state.turn_number
        state.mode = SessionMode.ROUNDS
        state.turn_number = 1
        if state.current_turn:
            state.current_turn.mode = SessionMode.ROUNDS
            state.current_turn.turn_number = 1
        state.updated_at = _now()
        return _ok(state, "Entering rounds!")

    def exit_rounds(self, state: GameState):
        """
        Return to exploration mode.
        Restores the exploration turn counter, advancing by 1 to account
        for the turn consumed by combat (standard B/X ruling).
        """
        if state.mode == SessionMode.EXPLORATION:
            return _err(state, "Already in exploration mode.")
        state.mode = SessionMode.EXPLORATION
        # Restore exploration turn number, +1 for the turn combat consumed
        resumed_at = (state.rounds_started_at_turn or state.turn_number) + 1
        state.turn_number = resumed_at
        state.rounds_started_at_turn = None
        if state.current_turn:
            state.current_turn.mode = SessionMode.EXPLORATION
            state.current_turn.turn_number = resumed_at
        state.updated_at = _now()
        return _ok(state, "Returning to exploration.")

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
            return _err(state, "Dungeons can only be imported before the session starts.")
        state.dungeon = dungeon
        # Replace the NPC roster if provided
        if npc_roster is not None:
            state.npc_roster = npc_roster
        # Point current_room_id at the entrance so the web UI has something
        # to show, but leave visited=False until the party actually enters.
        if dungeon.entrance_id and dungeon.entrance_id in dungeon.rooms:
            state.current_room_id = dungeon.entrance_id
        state.updated_at = _now()
        room_count = len(dungeon.rooms)
        return _ok(state, f"Dungeon '{dungeon.name}' loaded ({room_count} room(s)).")


def _now():
    """Get current UTC datetime."""
    from datetime import UTC, datetime
    return datetime.now(UTC)
