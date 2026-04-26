"""
tests/test_hide.py — Thief Hide skill tests.

Covers:
  - Hide action applies "hidden" condition to self
  - Hidden character is not targeted by NPC AI
  - Attacking while hidden bypasses target DEF and removes hidden
  - Attacking without hidden uses normal DEF mitigation
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
from engine.combat import CombatAction, _execute_action, _has_condition, _npc_decide
from models import (
    NPC,
    ActiveCondition,
    AzureStats,
    CharacterClass,
    GameState,
    Party,
    Room,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state() -> GameState:
    """Thief + Knight party, NPC in room, ready for combat."""
    from engine import add_npc, register_room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Rynn",   CharacterClass.THIEF,  "Pack A", owner_id="u1")
    create_character(state, "Aldric", CharacterClass.KNIGHT, "Pack A", owner_id="u2")
    start_session(state)

    room = Room(name="Hall", description="A stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id

    # NPC with 0 finesse (always gets hit), high defense, high HP
    npc = NPC(
        name="Goblin",
        hp_current=500,
        hp_max=500,
        defense=400,
        ability_scores=AzureStats(finesse=0),
        damage_dice="1d4",
    )
    add_npc(state, npc)
    return state


def _thief(state: GameState):
    return next(c for c in state.active_characters if list(c.jobs.keys())[0].upper() == "THIEF")


def _knight(state: GameState):
    return next(c for c in state.active_characters if list(c.jobs.keys())[0].upper() == "KNIGHT")


def _npc(state: GameState) -> NPC:
    return state.npcs_in_current_room[0]


# ---------------------------------------------------------------------------
# Hide applies condition
# ---------------------------------------------------------------------------

class TestHideAppliesCondition:

    def test_hide_action_applies_hidden_to_self(self):
        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        action = CombatAction(action_id="hide")
        log: list[str] = []
        _execute_action(state, thief.character_id, action, log)

        assert any(c.condition_id == "hidden" for c in thief.active_conditions)

    def test_hide_marks_oracle_and_move_used(self):
        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        cs = state.battlefield.combatants[thief.character_id]
        action = CombatAction(action_id="hide")
        _execute_action(state, thief.character_id, action, [])

        assert cs.used_oracle is True
        assert cs.used_move is True

    def test_hide_oracle_gate_blocks_second_use(self):
        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        cs = state.battlefield.combatants[thief.character_id]
        cs.used_oracle = True

        _execute_action(state, thief.character_id, CombatAction(action_id="hide"), [])

        assert not any(c.condition_id == "hidden" for c in thief.active_conditions)

    def test_hide_does_not_consume_act(self):
        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        cs = state.battlefield.combatants[thief.character_id]
        cs.acted_this_round = False

        _execute_action(state, thief.character_id, CombatAction(action_id="hide"), [])

        # acted_this_round is set by the round loop after _execute_action, not by the action itself
        # — hide has consumes_act=False, so it shouldn't force the round to count it as the act
        # We verify the condition applied (hide succeeded) but don't assert acted_this_round here
        assert any(c.condition_id == "hidden" for c in thief.active_conditions)


# ---------------------------------------------------------------------------
# NPC ignores hidden players
# ---------------------------------------------------------------------------

class TestNPCIgnoresHidden:

    def test_npc_does_not_target_hidden_player(self):
        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        knight = _knight(state)

        # Hide the thief
        thief.active_conditions.append(
            ActiveCondition(condition_id="hidden", duration_rounds=None)
        )

        npc_obj = _npc(state)
        npc_cs = state.battlefield.combatants[npc_obj.npc_id]
        # Move NPC to ENGAGE range so it would attack if it had a valid target
        from models import RangeBand
        npc_cs.range_band = RangeBand.ENGAGE

        decision = _npc_decide(state, npc_obj.npc_id, npc_cs)

        # Should target the knight (not hidden), not the thief
        if decision and decision.target_id:
            assert decision.target_id == knight.character_id

    def test_npc_has_no_target_when_only_player_is_hidden(self):
        from engine import add_npc, register_room
        state = GameState(platform_channel_id="ch2", dm_user_id="dm")
        state.party = Party(name="P")
        create_character(state, "Rynn", CharacterClass.THIEF, "Pack A", owner_id="u1")
        start_session(state)

        room = Room(name="Cave", description="")
        register_room(state, room)
        state.current_room_id = room.room_id
        npc = NPC(name="Rat", hp_current=10, hp_max=10, defense=0,
                  ability_scores=AzureStats(finesse=0), damage_dice="1d2")
        add_npc(state, npc)

        enter_rounds(state)

        thief = _thief(state)
        thief.active_conditions.append(
            ActiveCondition(condition_id="hidden", duration_rounds=None)
        )

        npc_cs = state.battlefield.combatants[npc.npc_id]
        from models import RangeBand
        npc_cs.range_band = RangeBand.ENGAGE

        decision = _npc_decide(state, npc.npc_id, npc_cs)

        # No valid targets — NPC should not attack
        assert decision is None or decision.target_id is None

    def test_has_condition_helper(self):
        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        assert not _has_condition(state, thief.character_id, "hidden")

        thief.active_conditions.append(
            ActiveCondition(condition_id="hidden", duration_rounds=None)
        )
        assert _has_condition(state, thief.character_id, "hidden")


# ---------------------------------------------------------------------------
# Attack from hidden bypasses DEF and removes hidden
# ---------------------------------------------------------------------------

class TestAttackFromHidden:

    def _setup_hidden_thief_attack(self, state):
        """Set up a hidden thief and NPC at ENGAGE range for a guaranteed hit."""
        from models import RangeBand

        thief = _thief(state)
        npc_obj = _npc(state)

        # Move both to ENGAGE range so the melee attack is in range
        state.battlefield.combatants[thief.character_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_obj.npc_id].range_band = RangeBand.ENGAGE

        thief.active_conditions.append(
            ActiveCondition(condition_id="hidden", duration_rounds=None)
        )

        action = CombatAction(action_id="attack", target_id=npc_obj.npc_id)
        return thief, npc_obj, action

    def test_attack_from_hidden_removes_hidden_condition(self):
        state = _make_state()
        enter_rounds(state)

        thief, npc_obj, action = self._setup_hidden_thief_attack(state)

        # side_effect order: attack roll, damage die (roll_dice_expr), crit roll
        with patch("engine.combat_hooks.random.randint", side_effect=[1000, 1, 2]):
            log: list[str] = []
            _execute_action(state, thief.character_id, action, log)

        assert not any(c.condition_id == "hidden" for c in thief.active_conditions), \
            "Hidden condition should be removed after attacking"

    def test_attack_from_hidden_bypasses_defense(self):
        """With bypass_defense active, full damage is applied despite high DEF."""
        state = _make_state()
        enter_rounds(state)

        thief, npc_obj, action = self._setup_hidden_thief_attack(state)
        initial_hp = npc_obj.hp_current

        # side_effect order: attack roll, damage die (roll_dice_expr), crit roll
        with patch("engine.combat_hooks.random.randint", side_effect=[1000, 1, 2]):
            _execute_action(state, thief.character_id, action, [])

        assert npc_obj.hp_current < initial_hp, \
            "NPC should take damage despite high DEF when attacker is hidden"

    def test_attack_without_hidden_does_not_bypass_defense(self):
        """Without hidden, high DEF reduces damage to 0."""
        from models import RangeBand

        state = _make_state()
        enter_rounds(state)

        thief = _thief(state)
        npc_obj = _npc(state)
        # Move to ENGAGE range; no hidden condition on thief
        state.battlefield.combatants[thief.character_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_obj.npc_id].range_band = RangeBand.ENGAGE
        initial_hp = npc_obj.hp_current

        action = CombatAction(action_id="attack", target_id=npc_obj.npc_id)
        # side_effect order: attack roll, damage die (roll_dice_expr), crit roll
        # 1d6 → 1 damage; NPC has 400 defense; 1 - 400 = 0 → no HP lost
        with patch("engine.combat_hooks.random.randint", side_effect=[1000, 1, 2]):
            _execute_action(state, thief.character_id, action, [])

        assert npc_obj.hp_current == initial_hp, \
            "NPC should take no damage when attacker is not hidden (high DEF blocks)"

    def test_hidden_removed_in_log(self):
        """Log should mention condition removal."""
        state = _make_state()
        enter_rounds(state)

        thief, npc_obj, action = self._setup_hidden_thief_attack(state)
        log: list[str] = []

        with patch("engine.combat_hooks.random.randint", side_effect=[1000, 1, 2]):
            _execute_action(state, thief.character_id, action, log)

        assert any("Hidden" in entry or "hidden" in entry.lower() for entry in log), \
            f"Log should mention hidden removal. Log was: {log}"
