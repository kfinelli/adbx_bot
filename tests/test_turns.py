"""
test_turns.py — Turn lifecycle tests: open, submit, close, resolve, auto-close.
"""

from engine import (
    close_turn,
    open_turn,
    resolve_turn,
    set_character_status,
    submit_turn,
)
from models import CharacterStatus, TurnStatus


class TestOpenTurn:
    def test_open_turn_succeeds(self, active_state):
        # active_state already has turn 1 open via start_session
        assert active_state.current_turn is not None
        assert active_state.current_turn.status == TurnStatus.OPEN

    def test_double_open_fails(self, active_state):
        result = open_turn(active_state)
        assert not result.ok
        assert "already open" in result.error.lower()

    def test_due_at_set_from_default(self, active_state):
        assert active_state.current_turn.due_at is not None


class TestSubmitTurn:
    def test_submit_succeeds(self, active_state):
        char_id = list(active_state.characters.keys())[0]
        result = submit_turn(active_state, char_id, "I search the room.")
        assert result.ok

    def test_submit_recorded(self, active_state):
        char_id = list(active_state.characters.keys())[0]
        submit_turn(active_state, char_id, "I search the room.")
        sub = active_state.latest_submission(char_id)
        assert sub is not None
        assert sub.action_text == "I search the room."

    def test_resubmit_supersedes_previous(self, active_party_state):
        # Use a multi-character state so the first submission does not
        # trigger auto-close (which would prevent resubmission).
        char_id = list(active_party_state.party.member_ids)[0]
        submit_turn(active_party_state, char_id, "First action.")
        submit_turn(active_party_state, char_id, "Changed my mind.")
        sub = active_party_state.latest_submission(char_id)
        assert sub.action_text == "Changed my mind."
        # Old submission is marked not latest
        old_subs = [
            s for s in active_party_state.current_turn.submissions
            if not s.is_latest
        ]
        assert len(old_subs) == 1
        assert old_subs[0].action_text == "First action."

    def test_submit_pre_start_fails(self, bare_state):
        """Cannot submit before the session is started."""
        from engine import create_character
        from models import CharacterClass
        create_character(bare_state, "Aldric", CharacterClass.FIGHTER, "Pack A")
        char_id = list(bare_state.characters.keys())[0]
        result = submit_turn(bare_state, char_id, "Action.")
        assert not result.ok

    def test_submit_on_hold_fails(self, active_state):
        from engine import hold_session
        hold_session(active_state)
        char_id = list(active_state.characters.keys())[0]
        result = submit_turn(active_state, char_id, "Action.")
        assert not result.ok

    def test_submit_dead_character_fails(self, active_state):
        char_id = list(active_state.characters.keys())[0]
        set_character_status(active_state, char_id, CharacterStatus.DEAD)
        result = submit_turn(active_state, char_id, "Ghost action.")
        assert not result.ok

    def test_submit_unknown_character_fails(self, active_state):
        from uuid import uuid4
        result = submit_turn(active_state, uuid4(), "Action.")
        assert not result.ok


class TestAutoClose:
    def test_auto_close_when_all_submitted(self, active_party_state):
        """Turn closes automatically once every active member submits."""
        char_ids = list(active_party_state.party.member_ids)
        # Submit all but the last — turn should still be open
        for cid in char_ids[:-1]:
            result = submit_turn(active_party_state, cid, "My action.")
            assert active_party_state.current_turn.status == TurnStatus.OPEN
        # Final submission — should auto-close and set notify_dm
        result = submit_turn(active_party_state, char_ids[-1], "Last action.")
        assert result.ok
        assert active_party_state.current_turn.status == TurnStatus.CLOSED
        assert result.notify_dm is True

    def test_dead_character_not_required_for_auto_close(self, active_party_state):
        """A dead character should not block auto-close."""
        char_ids = list(active_party_state.party.member_ids)
        # Kill the third character
        set_character_status(active_party_state, char_ids[2], CharacterStatus.DEAD)
        # Submit for the two active ones
        submit_turn(active_party_state, char_ids[0], "Action.")
        result = submit_turn(active_party_state, char_ids[1], "Action.")
        assert active_party_state.current_turn.status == TurnStatus.CLOSED
        assert result.notify_dm is True


class TestCloseTurn:
    def test_close_open_turn_succeeds(self, active_state):
        result = close_turn(active_state)
        assert result.ok
        assert active_state.current_turn.status == TurnStatus.CLOSED

    def test_close_already_closed_fails(self, active_state):
        close_turn(active_state)
        result = close_turn(active_state)
        assert not result.ok

    def test_close_with_no_turn_fails(self, bare_state):
        result = close_turn(bare_state)
        assert not result.ok


class TestResolveTurn:
    def test_resolve_succeeds(self, active_state):
        close_turn(active_state)
        result = resolve_turn(active_state, "You find a locked chest.")
        assert result.ok

    def test_resolve_advances_turn_number(self, active_state):
        close_turn(active_state)
        assert active_state.turn_number == 1
        resolve_turn(active_state, "Narrative.")
        assert active_state.turn_number == 2

    def test_resolve_moves_turn_to_history(self, active_state):
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")
        assert active_state.current_turn is None
        assert len(active_state.turn_history) == 1

    def test_resolve_clears_say_log(self, active_state):
        from engine import say
        say(active_state, "Aldric", "Hello!")
        assert len(active_state.say_log) == 1
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")
        assert active_state.say_log == []

    def test_resolve_open_turn_also_works(self, active_state):
        """DM can resolve an open turn directly (skipping explicit close)."""
        result = resolve_turn(active_state, "Narrative.")
        assert result.ok

    def test_resolve_stores_resolution_text(self, active_state):
        close_turn(active_state)
        resolve_turn(active_state, "You hear footsteps.")
        assert active_state.turn_history[0].resolution == "You hear footsteps."

    def test_resolve_with_no_turn_fails(self, bare_state):
        result = resolve_turn(bare_state, "Narrative.")
        assert not result.ok
