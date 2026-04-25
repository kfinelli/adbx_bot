"""
tests/test_slip.py — Thief Slip skill tests.

Covers:
  - Successful FNS check applies slipping condition and moves thief
  - Failed FNS check: no condition, no movement
  - Slip consumes the move action, not the ACT action
  - Slipping condition suppresses opportunity attacks
  - Level gate: Slip only available at thief level >= 3
  - slipping condition survives a persistence round-trip
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    create_character,
    enter_rounds,
    start_session,
)
from engine.combat import CombatAction, _execute_action
from models import (
    NPC,
    AzureStats,
    CharacterClass,
    GameState,
    Party,
    RangeBand,
    Room,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state() -> tuple[GameState, object, object]:
    """Thief + NPC at ENGAGE, thief has 0 finesse for deterministic roll math."""
    from engine import add_npc, register_room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Rynn", CharacterClass.THIEF, "Pack A", owner_id="u1")
    start_session(state)

    room = Room(name="Hall", description="A stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id

    npc = NPC(
        name="Goblin",
        hp_current=200,
        hp_max=200,
        defense=0,
        ability_scores=AzureStats(finesse=0),
        damage_dice="1d6",
    )
    add_npc(state, npc)
    enter_rounds(state)

    thief = next(c for c in state.active_characters
                 if list(c.jobs.keys())[0].upper() == "THIEF")
    # Fix finesse to 0 so roll alone determines check outcome
    thief.ability_scores = AzureStats(finesse=0)

    # Place both at ENGAGE so opportunity attacks would fire on movement
    state.battlefield.combatants[thief.character_id].range_band = RangeBand.ENGAGE
    state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE

    return state, thief, npc


def _thief(state: GameState):
    return next(c for c in state.active_characters
                if list(c.jobs.keys())[0].upper() == "THIEF")


# ---------------------------------------------------------------------------
# FNS check success
# ---------------------------------------------------------------------------

class TestSlipSuccess:

    def test_success_applies_slipping_condition(self):
        state, thief, _ = _make_state()
        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)
        # roll=900 + finesse=0 = 900 >= 900 → success
        with patch("engine.combat_hooks.random.randint", return_value=900):
            _execute_action(state, thief.character_id, action, [])

        assert any(c.condition_id == "slipping" for c in thief.active_conditions)

    def test_success_moves_thief(self):
        state, thief, _ = _make_state()
        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)
        with patch("engine.combat_hooks.random.randint", return_value=900):
            _execute_action(state, thief.character_id, action, [])

        cs = state.battlefield.combatants[thief.character_id]
        assert cs.range_band == RangeBand.CLOSE_MINUS

    def test_success_suppresses_opportunity_attacks(self):
        """NPC does not fire an opportunity attack when thief has slipping condition."""
        state, thief, npc = _make_state()
        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)

        log: list[str] = []
        # roll=900 → stat check succeeds; randint also used in attack roll if opp fires
        with patch("engine.combat_hooks.random.randint", return_value=900):
            _execute_action(state, thief.character_id, action, log)

        assert not any("opportunity attack" in line.lower() for line in log)


# ---------------------------------------------------------------------------
# FNS check failure
# ---------------------------------------------------------------------------

class TestSlipFailure:

    def test_failure_does_not_apply_condition(self):
        state, thief, _ = _make_state()
        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)
        # roll=1 + finesse=0 = 1 < 900 → failure
        with patch("engine.combat_hooks.random.randint", return_value=1):
            _execute_action(state, thief.character_id, action, [])

        assert not any(c.condition_id == "slipping" for c in thief.active_conditions)

    def test_failure_does_not_move_thief(self):
        state, thief, _ = _make_state()
        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)
        with patch("engine.combat_hooks.random.randint", return_value=1):
            _execute_action(state, thief.character_id, action, [])

        cs = state.battlefield.combatants[thief.character_id]
        assert cs.range_band == RangeBand.ENGAGE


# ---------------------------------------------------------------------------
# Action resource consumption
# ---------------------------------------------------------------------------

class TestSlipResourceConsumption:

    def test_slip_consumes_move_not_act(self):
        state, thief, _ = _make_state()
        cs = state.battlefield.combatants[thief.character_id]
        cs.acted_this_round = False

        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)
        with patch("engine.combat_hooks.random.randint", return_value=900):
            _execute_action(state, thief.character_id, action, [])

        assert cs.used_move is True
        assert cs.acted_this_round is False

    def test_slip_failure_still_consumes_move(self):
        state, thief, _ = _make_state()
        cs = state.battlefield.combatants[thief.character_id]

        action = CombatAction(action_id="slip", destination=RangeBand.CLOSE_MINUS)
        with patch("engine.combat_hooks.random.randint", return_value=1):
            _execute_action(state, thief.character_id, action, [])

        assert cs.used_move is True


# ---------------------------------------------------------------------------
# Level gate
# ---------------------------------------------------------------------------

class TestSlipLevelGate:

    def test_slip_unavailable_below_level_3(self):
        from engine.character import CharacterManager
        state = GameState(platform_channel_id="ch2", dm_user_id="dm")
        state.party = Party(name="P")
        create_character(state, "Rynn", CharacterClass.THIEF, "Pack A", owner_id="u1")
        start_session(state)
        thief = next(c for c in state.active_characters
                     if list(c.jobs.keys())[0].upper() == "THIEF")

        # Default level is 1; thief_slip requires level 3
        thief_job_key = list(thief.jobs.keys())[0]
        thief.jobs[thief_job_key].level = 2
        active_skills = CharacterManager.get_active_skills(thief)
        action_ids = {s.action_id for s in active_skills if s.action_id}
        assert "slip" not in action_ids

    def test_slip_available_at_level_3(self):
        from engine.character import CharacterManager
        state = GameState(platform_channel_id="ch3", dm_user_id="dm")
        state.party = Party(name="P")
        create_character(state, "Rynn", CharacterClass.THIEF, "Pack A", owner_id="u1")
        start_session(state)
        thief = next(c for c in state.active_characters
                     if list(c.jobs.keys())[0].upper() == "THIEF")

        thief_job_key = list(thief.jobs.keys())[0]
        thief.jobs[thief_job_key].level = 3
        active_skills = CharacterManager.get_active_skills(thief)
        action_ids = {s.action_id for s in active_skills if s.action_id}
        assert "slip" in action_ids


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestSlipPersistence:

    def test_slipping_condition_survives_round_trip(self, tmp_path):
        import tempfile

        from engine import apply_condition
        from persistence import Database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        db = Database(db_path)
        try:
            state = GameState(platform_channel_id="ch4", dm_user_id="dm")
            state.party = Party(name="P")
            create_character(state, "Rynn", CharacterClass.THIEF, "Pack A", owner_id="u1")
            start_session(state)
            char = next(c for c in state.active_characters
                        if list(c.jobs.keys())[0].upper() == "THIEF")

            apply_condition(state, char.character_id, "slipping", duration=1)
            assert any(c.condition_id == "slipping" for c in char.active_conditions)

            db.save_character(char)
            reloaded = db.load_character(str(char.character_id))

            assert any(c.condition_id == "slipping" for c in reloaded.active_conditions)
            assert reloaded.active_conditions[0].duration_rounds == 1
        finally:
            db.close()
            import os
            os.unlink(db_path)
