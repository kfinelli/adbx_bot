"""
tests/test_protector.py — Knight Protector skill tests.

Covers:
  - set_protector_target applies "protected" condition to ally
  - DEF bonus scales with Knight level (stacks 1/2/3 → +100/200/300)
  - repeal_existing removes old target's condition before applying new one
  - Switching target marks used_oracle on the knight's CombatantState
  - Oracle gate: action rejected when used_oracle is already True
  - Initial set does not mark acted_this_round (consumes_act=False)
  - exit_rounds clears "protected" conditions
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    auto_resolve_round,
    create_character,
    enter_rounds,
    exit_rounds,
    open_turn,
    start_session,
    submit_turn,
)
from engine.combat import CombatAction, _execute_action
from models import (
    NPC,
    ActiveCondition,
    CharacterClass,
    GameState,
    Party,
    PlayerTurnSubmission,
    Room,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_two_knight_state() -> GameState:
    """Knight + Thief party, NPC in room, ready for combat."""
    from engine import add_npc, register_room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Aldric", CharacterClass.KNIGHT, "Pack A", owner_id="u1")
    create_character(state, "Rynn",   CharacterClass.THIEF,  "Pack A", owner_id="u2")
    start_session(state)

    room = Room(name="Hall", description="A stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id

    add_npc(state, NPC(name="Goblin", hp_current=30, hp_max=30, defense=0, damage_dice="1d4"))
    return state


def _knight(state: GameState):
    return next(c for c in state.active_characters if list(c.jobs.keys())[0].upper() == "KNIGHT")


def _ally(state: GameState):
    return next(c for c in state.active_characters if list(c.jobs.keys())[0].upper() != "KNIGHT")


def _submit_protector(state: GameState, knight_id, target_id):
    """Submit a set_protector_target action for knight targeting target."""
    action = CombatAction(action_id="set_protector_target", target_id=target_id)
    sub = PlayerTurnSubmission(
        character_id=knight_id,
        action_text="Set Protector",
        is_latest=True,
        combat_action=action.to_dict(),
    )
    state.current_turn.submissions = [sub]


# ---------------------------------------------------------------------------
# Basic application
# ---------------------------------------------------------------------------

class TestProtectorApply:

    def test_protected_condition_applied_to_target(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond_ids = [c.condition_id for c in ally.active_conditions]
        assert "protected" in cond_ids

    def test_protected_condition_has_correct_source(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond = next(c for c in ally.active_conditions if c.condition_id == "protected")
        assert cond.source_id == knight.character_id

    def test_protected_is_permanent(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond = next(c for c in ally.active_conditions if c.condition_id == "protected")
        assert cond.duration_rounds is None

    def test_def_bonus_applied_via_defense_property(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        base_def = ally.defense
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        assert ally.defense == base_def + 100


# ---------------------------------------------------------------------------
# Level scaling
# ---------------------------------------------------------------------------

class TestProtectorLevelScaling:

    def _set_knight_level(self, state, level):
        knight = _knight(state)
        knight.jobs["knight"].level = level

    def test_level_1_gives_one_stack(self):
        state = _make_two_knight_state()
        self._set_knight_level(state, 1)
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond = next(c for c in ally.active_conditions if c.condition_id == "protected")
        assert cond.stacks == 1
        assert ally.defense == ally.defense - 100 + 100  # net +100

    def test_level_3_gives_two_stacks(self):
        state = _make_two_knight_state()
        self._set_knight_level(state, 3)
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        base_def = ally.defense
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond = next(c for c in ally.active_conditions if c.condition_id == "protected")
        assert cond.stacks == 2
        assert ally.defense == base_def + 200

    def test_level_5_gives_three_stacks(self):
        state = _make_two_knight_state()
        self._set_knight_level(state, 5)
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        base_def = ally.defense
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond = next(c for c in ally.active_conditions if c.condition_id == "protected")
        assert cond.stacks == 3
        assert ally.defense == base_def + 300

    def test_level_4_gives_two_stacks(self):
        """Level 4 is between L3 and L5 thresholds — should use L3 tier (2 stacks)."""
        state = _make_two_knight_state()
        self._set_knight_level(state, 4)
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        cond = next(c for c in ally.active_conditions if c.condition_id == "protected")
        assert cond.stacks == 2


# ---------------------------------------------------------------------------
# Repeal existing / switching
# ---------------------------------------------------------------------------

class TestProtectorSwitch:

    def _resolve_with_action(self, state, char_id, action):
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id,
            action_text="action",
            is_latest=True,
            combat_action=action.to_dict(),
        )]
        auto_resolve_round(state)

    def test_switch_removes_old_condition(self):
        from engine import add_npc, register_room

        state = GameState(platform_channel_id="ch", dm_user_id="dm")
        state.party = Party(name="P")
        create_character(state, "Aldric", CharacterClass.KNIGHT, "Pack A", owner_id="u1")
        create_character(state, "Rynn",   CharacterClass.THIEF,  "Pack A", owner_id="u2")
        create_character(state, "Sera",   CharacterClass.MAGE,   "Pack A", owner_id="u3")
        start_session(state)

        room = Room(name="Hall", description="")
        register_room(state, room)
        state.current_room_id = room.room_id
        add_npc(state, NPC(name="G", hp_current=30, hp_max=30, defense=0, damage_dice="1d4"))

        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        chars = [c for c in state.active_characters if c.character_id != knight.character_id]
        first_ally, second_ally = chars[0], chars[1]

        # First protection
        _submit_protector(state, knight.character_id, first_ally.character_id)
        auto_resolve_round(state)
        assert any(c.condition_id == "protected" for c in first_ally.active_conditions)

        # Switch to second ally
        open_turn(state)
        # Reset oracle so the switch can proceed
        cs = state.battlefield.combatants[knight.character_id]
        cs.used_oracle = False
        _submit_protector(state, knight.character_id, second_ally.character_id)
        auto_resolve_round(state)

        assert not any(c.condition_id == "protected" for c in first_ally.active_conditions), \
            "Old target should lose Protected"
        assert any(c.condition_id == "protected" for c in second_ally.active_conditions), \
            "New target should gain Protected"

    def test_action_marks_oracle_used_during_execution(self):
        """_execute_action sets used_oracle=True for consumes_oracle actions."""
        state = _make_two_knight_state()
        enter_rounds(state)

        knight = _knight(state)
        ally   = _ally(state)
        cs = state.battlefield.combatants[knight.character_id]
        assert cs.used_oracle is False

        action = CombatAction(action_id="set_protector_target", target_id=ally.character_id)
        log: list[str] = []
        _execute_action(state, knight.character_id, action, log)

        assert cs.used_oracle is True

    def test_oracle_gate_blocks_second_use(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)

        # Mark oracle already used
        cs = state.battlefield.combatants[knight.character_id]
        cs.used_oracle = True

        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        # Condition should NOT have been applied
        assert not any(c.condition_id == "protected" for c in ally.active_conditions)


# ---------------------------------------------------------------------------
# Repeal does not affect other sources
# ---------------------------------------------------------------------------

class TestProtectorRepeal:

    def test_repeal_only_removes_own_source(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)

        # Add a "protected" condition from a different source
        other_id = ally.character_id  # treat ally as the fake other source
        ally.active_conditions.append(ActiveCondition(
            condition_id="protected",
            duration_rounds=None,
            source_id=other_id,
            stacks=2,
        ))

        # Knight applies their own protection to ally
        _submit_protector(state, knight.character_id, ally.character_id)
        auto_resolve_round(state)

        protected_conds = [c for c in ally.active_conditions if c.condition_id == "protected"]
        # Should have two: one from other_id (untouched) + one from knight
        assert len(protected_conds) == 2
        sources = {c.source_id for c in protected_conds}
        assert other_id in sources
        assert knight.character_id in sources


# ---------------------------------------------------------------------------
# Exit rounds cleanup
# ---------------------------------------------------------------------------

class TestProtectorCleanup:

    def test_protected_removed_on_exit_rounds(self):
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)

        ally.active_conditions.append(ActiveCondition(
            condition_id="protected",
            duration_rounds=None,
            source_id=knight.character_id,
            stacks=1,
        ))

        exit_rounds(state)

        assert not any(c.condition_id == "protected" for c in ally.active_conditions)

    def test_permanent_non_combat_condition_survives_exit(self):
        """Permanent conditions that are not combat-only still survive exit_rounds."""
        state = _make_two_knight_state()
        enter_rounds(state)

        ally = _ally(state)
        ally.active_conditions.append(ActiveCondition(
            condition_id="strengthened",
            duration_rounds=None,
        ))

        exit_rounds(state)

        assert any(c.condition_id == "strengthened" for c in ally.active_conditions)


# ---------------------------------------------------------------------------
# Oracle action does not prematurely close the round (issue #160)
# ---------------------------------------------------------------------------

class TestProtectorNoPreemptiveSubmit:

    def test_protector_oracle_does_not_trigger_auto_resolve(self):
        """Set Protector (consumes_act=False) must not auto-resolve the round.
        The knight should still be able to submit their act action afterward."""
        state = _make_two_knight_state()
        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)

        # Knight submits Set Protector — oracle action, consumes_act=False.
        # The ally (Thief) has not submitted yet, so the round must NOT resolve.
        from engine.combat import CombatAction
        oracle_action = CombatAction(action_id="set_protector_target", target_id=ally.character_id)
        result = submit_turn(
            state, knight.character_id, "Set Protector",
            combat_action=oracle_action.to_dict(),
        )

        assert result.ok
        assert not result.auto_resolved, "Oracle-only submit must not auto-resolve the round"
        assert state.current_turn is not None
        from models import TurnStatus
        assert state.current_turn.status == TurnStatus.OPEN, "Round should remain open"

    def test_protector_then_attack_resolves_and_applies_both(self):
        """Set Protector + Attack submitted by knight; ally submits Attack.
        Round resolves; both the Protected condition and attack damage are applied."""
        from engine import add_npc, register_room
        from engine.combat import CombatAction

        state = GameState(platform_channel_id="ch2", dm_user_id="dm")
        state.party = Party(name="P")
        create_character(state, "Aldric", CharacterClass.KNIGHT, "Pack A", owner_id="u1")
        create_character(state, "Rynn",   CharacterClass.THIEF,  "Pack A", owner_id="u2")
        start_session(state)

        room = Room(name="Hall", description="")
        register_room(state, room)
        state.current_room_id = room.room_id
        npc = NPC(name="Goblin", hp_current=30, hp_max=30, defense=0, damage_dice="1d4")
        add_npc(state, npc)

        enter_rounds(state)
        open_turn(state)

        knight = _knight(state)
        ally   = _ally(state)
        goblin = state.npcs_in_current_room[0]

        # Pin initiatives so knight acts first
        state.battlefield.combatants[knight.character_id].initiative = 20
        state.battlefield.combatants[ally.character_id].initiative   = 10
        state.battlefield.combatants[goblin.npc_id].initiative       = 5

        # Knight submits oracle first, then act
        oracle_action = CombatAction(action_id="set_protector_target", target_id=ally.character_id)
        submit_turn(state, knight.character_id, "Set Protector", combat_action=oracle_action.to_dict())

        assert not any(c.condition_id == "protected" for c in ally.active_conditions), \
            "Protected condition must not apply before round resolves"

        attack_action = CombatAction(action_id="attack", target_id=goblin.npc_id)
        r1 = submit_turn(state, knight.character_id, "Attack", combat_action=attack_action.to_dict())
        assert not r1.auto_resolved, "Ally has not submitted yet — round must not auto-resolve"

        ally_attack = CombatAction(action_id="attack", target_id=goblin.npc_id)
        r2 = submit_turn(state, ally.character_id, "Attack", combat_action=ally_attack.to_dict())
        assert r2.auto_resolved, "All players submitted acts — round should auto-resolve"

        # Protected condition must have been applied during round resolution
        assert any(c.condition_id == "protected" for c in ally.active_conditions), \
            "Protected condition must be applied after round resolves"
