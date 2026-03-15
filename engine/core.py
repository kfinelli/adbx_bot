"""
Turn management for the dungeon crawler engine.
"""

from datetime import UTC, datetime, timedelta

from models import (
    GameState,
    PlayerTurnSubmission,
    SessionMode,
    TurnRecord,
    TurnStatus,
)

from .helpers import _err, _ok, _snapshot
from .light import _tick_light


def _now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)


class TurnManager:
    """Manages game turns and rounds."""

    def open_turn(
        self,
        state:  GameState,
        due_at: datetime | None = None,
    ):
        """
        Open a new dungeon turn (or combat round).
        If due_at is not provided, uses state.default_turn_hours from now.
        Fails if a turn is already open.
        """
        if state.current_turn is not None and state.current_turn.status == TurnStatus.OPEN:
            return _err(state, "A turn is already open. Close or resolve it first.")

        if due_at is None:
            due_at = _now() + timedelta(hours=state.default_turn_hours)

        turn = TurnRecord(
            turn_number=state.turn_number,
            mode=state.mode,
            status=TurnStatus.OPEN,
            opened_at=_now(),
            due_at=due_at,
        )
        state.current_turn = turn
        state.updated_at = _now()
        return _ok(state, f"Turn {state.turn_number} is now open.")

    def submit_turn(
        self,
        state:        GameState,
        character_id,
        action_text:  str,
    ):
        """
        Submit (or resubmit) a player's action for the current open turn.
        Previous submissions by this character are marked superseded.
        """
        if not state.session_active:
            return _err(state, "The session is on hold.")
        if state.mode == SessionMode.PRE_START:
            return _err(state, "The session has not started yet.")
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            mode_str = "round" if state.mode == SessionMode.ROUNDS else "turn"
            return _err(state, f"No open {mode_str} to submit to. The DM needs to resolve the previous {mode_str} first.")

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        if char.status.value != "active":
            return _err(state, f"{char.name} is not active and cannot submit a turn.")

        # Supersede any prior submissions from this character
        for sub in state.current_turn.submissions:
            if sub.character_id == character_id:
                sub.is_latest = False

        state.current_turn.submissions.append(PlayerTurnSubmission(
            character_id=character_id,
            submitted_at=_now(),
            action_text=action_text,
            is_latest=True,
        ))
        state.updated_at = _now()

        # Auto-close if all active party members have submitted
        if state.party is not None:
            submitted_ids = {
                s.character_id for s in state.current_turn.submissions if s.is_latest
            }
            active_ids = {
                cid for cid in state.party.member_ids
                if state.characters.get(cid) and
                   state.characters[cid].status.value == "active"
            }
            if active_ids and active_ids.issubset(submitted_ids):
                state.current_turn.status = TurnStatus.CLOSED
                state.current_turn.closed_at = _now()
                return _ok(state, f"{char.name}: \"{action_text}\"", notify_dm=True)

        return _ok(state, f"{char.name}: \"{action_text}\"")

    def close_turn(self, state: GameState):
        """
        Close the current turn for submissions (DM is now arbitrating).
        Does not yet resolve or advance the turn counter.
        """
        if state.current_turn is None:
            return _err(state, "No current turn to close.")
        if state.current_turn.status != TurnStatus.OPEN:
            return _err(state, "Current turn is not open.")

        state.current_turn.status = TurnStatus.CLOSED
        state.current_turn.closed_at = _now()
        state.updated_at = _now()
        return _ok(state, f"Turn {state.turn_number} closed. Awaiting DM resolution.")

    def resolve_turn(
        self,
        state:      GameState,
        resolution: str,
    ):
        """
        DM resolves the current turn with a narrative description.
        Snapshots state, moves turn to history, advances turn counter,
        and ticks down the active light source.
        """
        if state.current_turn is None:
            return _err(state, "No current turn to resolve.")
        if state.current_turn.status not in (TurnStatus.OPEN, TurnStatus.CLOSED):
            return _err(state, "Current turn is already resolved.")

        turn = state.current_turn

        # Snapshot state before mutation (shallow-ish; good enough for history log)
        turn.state_snapshot = _snapshot(state)
        turn.resolution     = resolution
        turn.status         = TurnStatus.RESOLVED
        turn.resolved_at    = _now()

        # Move to history
        state.turn_history.append(turn)
        state.current_turn = None

        # Clear say log and reset oracle counter for next turn
        state.say_log = []
        state.oracle_counter = 0

        # Advance turn counter and tick light source (exploration mode only)
        state.turn_number += 1
        if state.mode == SessionMode.EXPLORATION:
            _tick_light(state)

        state.updated_at = _now()
        return _ok(state, resolution)

    def set_turn_number(self, state: GameState, turn_number: int):
        """Directly set the session turn counter. DM correction tool."""
        if turn_number < 0:
            return _err(state, "Turn number cannot be negative.")
        state.turn_number = turn_number
        if state.current_turn:
            state.current_turn.turn_number = turn_number
        state.updated_at = _now()
        return _ok(state, f"Turn number set to {turn_number}.")
