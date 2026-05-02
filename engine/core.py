"""
Turn management for the dungeon crawler engine.
"""

from datetime import datetime, timedelta

from models import (
    GameState,
    PlayerTurnSubmission,
    SessionMode,
    TurnRecord,
    TurnStatus,
)

from .data_loader import ACTION_REGISTRY
from .helpers import _err, _now, _ok, _snapshot
from .light import _tick_light
from .strings import fmt_string, get_string


def _sub_consumes_act(sub: "PlayerTurnSubmission") -> bool:
    """Return True if this submission's action consumes the act resource."""
    if sub.combat_action is None:
        return True  # free-text / Affect submissions always count as the act
    action_def = ACTION_REGISTRY.get(sub.combat_action.get("action_id", ""))
    return action_def is None or action_def.consumes_act


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
            return _err(state, get_string("turn.errors.already_open"))

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
        if state.battlefield is not None:
            state.battlefield.round_log = []
        state.updated_at = _now()
        return _ok(state, fmt_string("turn.opened", turn_number=state.turn_number))

    def submit_turn(
        self,
        state:         GameState,
        character_id,
        action_text:   str,
        combat_action: dict | None = None,
    ):
        """
        Submit (or resubmit) a player's action for the current open turn.
        Previous submissions by this character are marked superseded.

        combat_action: optional plain-dict representation of a CombatAction
            (from CombatAction.to_dict()).  When all active players have
            submitted structured (non-Affect) combat actions in ROUNDS mode,
            the round is auto-resolved immediately without DM intervention.
        """
        if not state.session_active:
            return _err(state, get_string("errors.session_on_hold"))
        if state.mode == SessionMode.PRE_START:
            return _err(state, get_string("errors.session_not_started"))
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            mode_str = "round" if state.mode == SessionMode.ROUNDS else "turn"
            return _err(state, fmt_string("turn.errors.no_open", mode_str=mode_str))

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        if char.status.value != "active":
            return _err(state, fmt_string("turn.errors.character_inactive", name=char.name))

        # Determine whether the incoming action consumes the act resource.
        new_consumes_act = True
        if combat_action is not None:
            action_def = ACTION_REGISTRY.get(combat_action.get("action_id", ""))
            if action_def is not None:
                new_consumes_act = action_def.consumes_act

        # Supersede prior submissions of the same type only.
        # A consumes_act=False submission (oracle/move) keeps its is_latest slot
        # so that auto_resolve_round can still process it alongside the act.
        for sub in state.current_turn.submissions:
            if sub.character_id != character_id:
                continue
            sub_consumes_act = True
            if sub.combat_action is not None:
                sub_def = ACTION_REGISTRY.get(sub.combat_action.get("action_id", ""))
                if sub_def is not None:
                    sub_consumes_act = sub_def.consumes_act
            if sub_consumes_act == new_consumes_act:
                sub.is_latest = False

        state.current_turn.submissions.append(PlayerTurnSubmission(
            character_id=character_id,
            submitted_at=_now(),
            action_text=action_text,
            is_latest=True,
            combat_action=combat_action,
        ))
        state.updated_at = _now()

        # --- Check whether all active members have now submitted ------------
        if state.party is None:
            return _ok(state, fmt_string("turn.submitted_action", name=char.name, action_text=action_text))

        # Only count players whose latest submission actually consumes the act.
        # Oracle-only submissions (consumes_act=False) don't close a player's turn.
        submitted_ids = {
            s.character_id for s in state.current_turn.submissions
            if s.is_latest and _sub_consumes_act(s)
        }
        active_ids = {
            cid for cid in state.party.member_ids
            if state.characters.get(cid) and
               state.characters[cid].status.value == "active"
        }

        if not (active_ids and active_ids.issubset(submitted_ids)):
            # Still waiting on other players
            return _ok(state, fmt_string("turn.submitted_action", name=char.name, action_text=action_text))

        # All submitted — check whether we can auto-resolve (ROUNDS mode only)
        if state.mode == SessionMode.ROUNDS:
            latest_subs = [
                s for s in state.current_turn.submissions if s.is_latest
            ]
            all_structured = all(
                s.combat_action is not None and
                ACTION_REGISTRY.get(s.combat_action.get("action_id", ""), None) is not None and
                ACTION_REGISTRY[s.combat_action["action_id"]].action_type != "affect"
                for s in latest_subs
            )
            if all_structured:
                return self._auto_resolve(state, char.name, action_text)

        # Exploration mode, or ROUNDS with at least one Affect — close for DM
        state.current_turn.status = TurnStatus.CLOSED
        state.current_turn.closed_at = _now()
        return _ok(state, fmt_string("turn.submitted_action", name=char.name, action_text=action_text), notify_dm=True)

    def _auto_resolve(
        self,
        state:       GameState,
        last_name:   str,
        action_text: str,
    ):
        """
        Auto-resolve a round where all players used structured combat actions.
        Calls auto_resolve_round(), then advances the turn as if the DM had
        called resolve_turn(), and immediately opens the next round so the
        Act button is never greyed out waiting for DM intervention.
        Returns notify_dm=False (no DM ping needed).
        """
        from .combat import auto_resolve_round

        result = auto_resolve_round(state)
        if not result.ok:
            # Fallback: hand to DM if auto-resolve errors
            state.current_turn.status = TurnStatus.CLOSED
            state.current_turn.closed_at = _now()
            return _ok(state, fmt_string("turn.submitted_action", name=last_name, action_text=action_text), notify_dm=True)

        narrative = result.message

        # Advance turn exactly as resolve_turn() does
        turn = state.current_turn
        turn.state_snapshot = _snapshot(state)
        turn.resolution     = narrative
        turn.status         = TurnStatus.RESOLVED
        turn.resolved_at    = _now()

        state.turn_history.append(turn)
        state.current_turn  = None
        state.say_log       = []
        state.oracle_counter = 0
        # exit_rounds (called on victory or abscond) already set turn_number;
        # only increment here when still in ROUNDS mode (combat continues).
        if state.mode == SessionMode.ROUNDS:
            state.turn_number += 1
        state.updated_at    = _now()

        # Open the next round immediately so the status message shows an open
        # turn and the Act button is enabled on the very next status post.
        # (The DM can still close/hold if they want to interject.)
        self.open_turn(state)

        result = _ok(state, narrative, notify_dm=False)
        result.auto_resolved = True
        return result

    def close_turn(self, state: GameState):
        """
        Close the current turn for submissions (DM is now arbitrating).
        Does not yet resolve or advance the turn counter.
        """
        if state.current_turn is None:
            return _err(state, get_string("turn.errors.no_current_turn"))
        if state.current_turn.status != TurnStatus.OPEN:
            return _err(state, get_string("turn.errors.not_open"))

        state.current_turn.status = TurnStatus.CLOSED
        state.current_turn.closed_at = _now()
        state.updated_at = _now()
        return _ok(state, fmt_string("turn.closed", turn_number=state.turn_number))

    def resolve_turn(
        self,
        state:      GameState,
        resolution: str,
        free_move:  bool = False,
    ):
        """
        DM resolves the current turn with a narrative description.
        Snapshots state, moves turn to history, advances turn counter,
        and ticks down the active light source.

        free_move=True skips the turn counter increment and light tick
        (used when the party moves to a previously-explored room at no cost).
        The TurnRecord is still appended to history.
        """
        if state.current_turn is None:
            return _err(state, get_string("turn.errors.no_current_to_resolve"))
        if state.current_turn.status not in (TurnStatus.OPEN, TurnStatus.CLOSED):
            return _err(state, get_string("turn.errors.already_resolved"))

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

        # Advance turn counter and tick light source (exploration mode only).
        # free_move bypasses both — movement to an explored room is free.
        if not free_move:
            state.turn_number += 1
            if state.mode == SessionMode.EXPLORATION:
                _tick_light(state)
                from .encounter import check_random_encounter
                enc = check_random_encounter(state)
                if enc is not None:
                    resolution = resolution + "\n" + enc.message

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
        if state.dungeon and state.dungeon.random_encounter_roster and state.mode == SessionMode.EXPLORATION:
            from .encounter import check_random_encounter
            enc = check_random_encounter(state)
            if enc is not None:
                return _ok(state, f"Turn number set to {turn_number}. {enc.message}")
        return _ok(state, fmt_string("turn.number_set", turn_number=turn_number))

    def unsubmit_turn(
        self,
        state: GameState,
        character_id,
    ):
        """
        Mark a player's latest turn submission as not latest (un-submit).
        Used when a player's action is invalid and needs to be re-done.
        Returns the character name for notification purposes.
        """
        if state.current_turn is None:
            return _err(state, get_string("turn.errors.no_current"))
        if state.current_turn.status != TurnStatus.OPEN:
            return _err(state, get_string("turn.errors.not_open_unsubmit"))

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        # Find and mark the latest submission from this character as not latest
        found = False
        for sub in state.current_turn.submissions:
            if sub.character_id == character_id and sub.is_latest:
                sub.is_latest = False
                found = True
                break

        if not found:
            return _err(state, fmt_string("turn.errors.no_submission", name=char.name))

        state.updated_at = _now()
        return _ok(state, fmt_string("turn.unsubmitted", name=char.name))


# Module-level convenience functions
def open_turn(*args, **kwargs):
    return TurnManager().open_turn(*args, **kwargs)


def submit_turn(*args, **kwargs):
    return TurnManager().submit_turn(*args, **kwargs)


def close_turn(*args, **kwargs):
    return TurnManager().close_turn(*args, **kwargs)


def resolve_turn(*args, **kwargs):
    return TurnManager().resolve_turn(*args, **kwargs)


def set_turn_number(*args, **kwargs):
    return TurnManager().set_turn_number(*args, **kwargs)


def unsubmit_turn(*args, **kwargs):
    return TurnManager().unsubmit_turn(*args, **kwargs)
