"""
test_session.py — Session lifecycle: start, hold, resume, rounds mode.
"""

from engine import (
    enter_rounds,
    exit_rounds,
    hold_session,
    resume_session,
    set_turn_number,
    start_session,
)
from models import SessionMode, TurnStatus


class TestStartSession:
    def test_start_succeeds_with_characters(self, state_with_fighter):
        result = start_session(state_with_fighter)
        assert result.ok
        assert state_with_fighter.mode == SessionMode.EXPLORATION
        assert state_with_fighter.session_active is True

    def test_start_opens_first_turn(self, state_with_fighter):
        start_session(state_with_fighter)
        assert state_with_fighter.current_turn is not None
        assert state_with_fighter.current_turn.status == TurnStatus.OPEN
        assert state_with_fighter.current_turn.turn_number == 1

    def test_start_fails_with_no_characters(self, bare_state):
        result = start_session(bare_state)
        assert not result.ok
        assert "No characters" in result.error

    def test_start_fails_if_already_started(self, active_state):
        result = start_session(active_state)
        assert not result.ok
        assert "already started" in result.error.lower()


class TestHoldResume:
    def test_hold_succeeds_on_active_session(self, active_state):
        result = hold_session(active_state)
        assert result.ok
        assert active_state.session_active is False

    def test_hold_fails_if_already_on_hold(self, active_state):
        hold_session(active_state)
        result = hold_session(active_state)
        assert not result.ok

    def test_resume_succeeds_from_hold(self, active_state):
        hold_session(active_state)
        result = resume_session(active_state)
        assert result.ok
        assert active_state.session_active is True

    def test_resume_fails_if_not_on_hold(self, active_state):
        result = resume_session(active_state)
        assert not result.ok


class TestModeSwitch:
    def test_enter_rounds_from_exploration(self, active_state):
        result = enter_rounds(active_state)
        assert result.ok
        assert active_state.mode == SessionMode.ROUNDS

    def test_enter_rounds_resets_turn_to_one(self, active_state):
        from engine import close_turn, resolve_turn
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")   # turn_number → 2
        assert active_state.turn_number == 2
        enter_rounds(active_state)
        assert active_state.turn_number == 1

    def test_enter_rounds_saves_exploration_turn(self, active_state):
        from engine import close_turn, resolve_turn
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")   # turn_number → 2
        enter_rounds(active_state)
        assert active_state.rounds_started_at_turn == 2

    def test_enter_rounds_fails_if_already_in_rounds(self, active_state):
        enter_rounds(active_state)
        result = enter_rounds(active_state)
        assert not result.ok

    def test_exit_rounds_restores_exploration(self, active_state):
        enter_rounds(active_state)
        result = exit_rounds(active_state)
        assert result.ok
        assert active_state.mode == SessionMode.EXPLORATION

    def test_exit_rounds_advances_exploration_turn(self, active_state):
        from engine import close_turn, resolve_turn
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")   # turn_number → 2
        enter_rounds(active_state)
        exit_rounds(active_state)
        # Should be exploration turn 3 (2 + 1 for combat turn consumed)
        assert active_state.turn_number == 3

    def test_exit_rounds_fails_if_in_exploration(self, active_state):
        result = exit_rounds(active_state)
        assert not result.ok


class TestSetTurnNumber:
    def test_set_turn_number_succeeds(self, active_state):
        result = set_turn_number(active_state, 5)
        assert result.ok
        assert active_state.turn_number == 5

    def test_set_turn_number_zero_allowed(self, active_state):
        result = set_turn_number(active_state, 0)
        assert result.ok
        assert active_state.turn_number == 0

    def test_set_turn_number_negative_rejected(self, active_state):
        result = set_turn_number(active_state, -1)
        assert not result.ok
