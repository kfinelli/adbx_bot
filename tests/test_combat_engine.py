"""
tests/test_combat_engine.py — Phase 2 combat engine tests.

Covers:
  - initialize_battlefield: correct starting positions and combatant registration
  - CombatAction: construction, to_dict / from_dict round-trip
  - auto_resolve_round: attack lands / misses, damage applied, NPC death,
    character death, move action, condition ticking
  - Auto-resolution trigger: fires when all structured submissions in
  - Affect submission: suppresses auto-resolve, hands to DM
  - apply_condition: validation, application, refresh, removal on expiry
  - enter_rounds: creates battlefield; exit_rounds: clears it
  - Serialization: battlefield survives round-trip after auto-resolve
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    apply_condition,
    auto_resolve_round,
    create_character,
    enter_rounds,
    exit_rounds,
    initialize_battlefield,
    open_turn,
    start_session,
    submit_turn,
)
from engine.azure_engine import CharacterClass
from engine.combat import CombatAction, _npc_decide, _tick_conditions
from models import (
    NPC,
    ActiveCondition,
    GameState,
    Party,
    RangeBand,
    SessionMode,
    TurnStatus,
)
from serialization import deserialize_state, serialize_state

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state_with_npc() -> GameState:
    """Active EXPLORATION state with one Fighter and one NPC in the current room."""
    from engine import add_npc, register_room
    from models import Room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Aldric", CharacterClass.KNIGHT, "Pack A", owner_id="u1")
    start_session(state)

    room = Room(name="Hall", description="A stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id

    add_npc(state, NPC(name="Goblin", hp_current=5, hp_max=5,
                       armor_class=7, damage_dice="1d6"))
    return state


def _make_party_state() -> GameState:
    """Two-character party (Fighter + Cleric) in EXPLORATION with a room NPC."""
    from engine import add_npc, register_room
    from models import Room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Aldric", CharacterClass.KNIGHT, "Pack A", owner_id="u1")
    create_character(state, "Tomas",  CharacterClass.MAGE,  "Pack A", owner_id="u2")
    start_session(state)

    room = Room(name="Hall", description="Stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id

    add_npc(state, NPC(name="Goblin", hp_current=8, hp_max=8,
                       armor_class=7, damage_dice="1d6"))
    return state


# ---------------------------------------------------------------------------
# CombatAction
# ---------------------------------------------------------------------------

class TestCombatAction:

    def test_is_affect_true_for_affect(self):
        assert CombatAction(action_id="affect").is_affect is True

    def test_is_affect_false_for_attack(self):
        assert CombatAction(action_id="attack").is_affect is False

    def test_to_dict_and_from_dict_attack(self):
        tid = uuid4()
        a = CombatAction(action_id="attack", target_id=tid, free_text="I strike!")
        d = a.to_dict()
        assert d["action_id"] == "attack"
        assert d["target_id"] == str(tid)
        assert d["destination"] is None

        a2 = CombatAction.from_dict(d)
        assert a2.action_id == "attack"
        assert a2.target_id == tid
        assert a2.destination is None

    def test_to_dict_and_from_dict_move(self):
        a = CombatAction(action_id="move", destination=RangeBand.ENGAGE)
        d = a.to_dict()
        assert d["destination"] == "engage"
        assert d["target_id"] is None

        a2 = CombatAction.from_dict(d)
        assert a2.destination == RangeBand.ENGAGE
        assert a2.target_id is None

    def test_from_dict_affect_no_target(self):
        a = CombatAction.from_dict({"action_id": "affect", "free_text": "I hide."})
        assert a.is_affect
        assert a.target_id is None
        assert a.destination is None


# ---------------------------------------------------------------------------
# initialize_battlefield
# ---------------------------------------------------------------------------

class TestInitializeBattlefield:

    def test_players_start_at_far_minus(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        cs = state.battlefield.combatants[char_id]
        assert cs.range_band == RangeBand.FAR_MINUS
        assert cs.is_player is True

    def test_npcs_start_at_far_plus(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        npc = state.npcs_in_current_room[0]
        cs = state.battlefield.combatants[npc.npc_id]
        assert cs.range_band == RangeBand.FAR_PLUS
        assert cs.is_player is False

    def test_all_active_chars_in_battlefield(self):
        state = _make_party_state()
        enter_rounds(state)
        char_ids = set(state.characters.keys())
        bf_ids   = set(state.battlefield.combatants.keys())
        assert char_ids.issubset(bf_ids)

    def test_npcs_in_battlefield(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        npc = state.npcs_in_current_room[0]
        assert npc.npc_id in state.battlefield.combatants

    def test_initiative_is_set(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        for cs in state.battlefield.combatants.values():
            assert isinstance(cs.initiative, int)

    def test_exit_rounds_clears_battlefield(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        assert state.battlefield is not None
        exit_rounds(state)
        assert state.battlefield is None

    def test_dead_char_excluded(self):
        state = _make_state_with_npc()
        char = list(state.characters.values())[0]
        from models import CharacterStatus
        char.status = CharacterStatus.DEAD
        bf = initialize_battlefield(state)
        assert char.character_id not in bf.combatants

    def test_dead_npc_excluded(self):
        state = _make_state_with_npc()
        npc = state.npcs_in_current_room[0]
        npc.status = "dead"
        bf = initialize_battlefield(state)
        assert npc.npc_id not in bf.combatants


# ---------------------------------------------------------------------------
# auto_resolve_round
# ---------------------------------------------------------------------------

class TestAutoResolveRound:

    def _setup_combat(self, npc_hp=5, npc_ac=1):
        """Enter rounds, open a turn, return (state, char_id, npc_id).

        Character HP is set to 20 so an NPC counter-attack before the player
        acts cannot kill the character and skip their action.
        """
        state = _make_state_with_npc()
        npc = state.npcs_in_current_room[0]
        npc.hp_current = npc_hp
        npc.hp_max = npc_hp
        npc.armor_class = npc_ac
        char = list(state.characters.values())[0]
        char.hp_current = 20
        char.hp_max = 20
        enter_rounds(state)
        open_turn(state)
        return state, list(state.characters.keys())[0], npc.npc_id

    def test_attack_hit_reduces_npc_hp(self):
        """AC=1 guarantees a hit (any roll >= 1)."""
        state, char_id, npc_id = self._setup_combat(npc_hp=20, npc_ac=1)
        # Place combatants in melee range
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_id].range_band  = RangeBand.ENGAGE

        action = CombatAction(action_id="attack", target_id=npc_id)
        state.current_turn.submissions[0] if state.current_turn.submissions else None
        # Manually set up: one player, one action

        from models import PlayerTurnSubmission
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id,
            action_text="Attack",
            is_latest=True,
            combat_action=action.to_dict(),
        )]

        result = auto_resolve_round(state)
        assert result.ok
        npc = state.npcs_in_current_room[0]
        assert npc.hp_current < 20, "NPC should have taken damage"

    def test_attack_miss_leaves_npc_hp_unchanged(self):
        """AC=21 guarantees a miss (max d20 roll is 20, needs roll >= AC to hit)."""
        state, char_id, npc_id = self._setup_combat(npc_hp=5, npc_ac=21)
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_id].range_band  = RangeBand.ENGAGE

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]

        result = auto_resolve_round(state)
        assert result.ok
        npc = state.npcs_in_current_room[0]
        assert npc.hp_current == 5

    def test_npc_death_sets_status(self):
        """1 HP NPC with AC=1 must die from any hit."""
        state, char_id, npc_id = self._setup_combat(npc_hp=1, npc_ac=1)
        # Guarantee the character can absorb the NPC's first strike (max 1d6 = 6 damage).
        state.characters[char_id].hp_current = 20
        state.characters[char_id].hp_max = 20
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_id].range_band  = RangeBand.ENGAGE

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]

        auto_resolve_round(state)
        npc = state.npcs_in_current_room[0]
        assert npc.status == "dead"
        assert npc_id not in state.battlefield.combatants

    def test_move_action_changes_range_band(self):
        state, char_id, npc_id = self._setup_combat()
        assert state.battlefield.combatants[char_id].range_band == RangeBand.FAR_MINUS

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="move", destination=RangeBand.CLOSE_MINUS)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Move",
            is_latest=True, combat_action=action.to_dict(),
        )]

        result = auto_resolve_round(state)
        assert result.ok
        assert state.battlefield.combatants[char_id].range_band == RangeBand.CLOSE_MINUS

    def test_auto_resolve_returns_narrative(self):
        state, char_id, npc_id = self._setup_combat(npc_ac=1)
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_id].range_band  = RangeBand.ENGAGE

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]

        result = auto_resolve_round(state)
        assert result.ok
        assert len(result.message) > 0

    def test_round_log_stored_on_battlefield(self):
        state, char_id, npc_id = self._setup_combat(npc_ac=1)
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc_id].range_band  = RangeBand.ENGAGE

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]

        auto_resolve_round(state)
        assert isinstance(state.battlefield.round_log, list)
        assert len(state.battlefield.round_log) > 0

    def test_acted_flags_reset_after_round(self):
        state, char_id, npc_id = self._setup_combat()
        state.battlefield.combatants[char_id].range_band = RangeBand.FAR_MINUS

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="move", destination=RangeBand.CLOSE_MINUS)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Move",
            is_latest=True, combat_action=action.to_dict(),
        )]

        auto_resolve_round(state)
        for cs in state.battlefield.combatants.values():
            assert cs.acted_this_round is False


# ---------------------------------------------------------------------------
# Auto-resolution trigger via submit_turn
# ---------------------------------------------------------------------------

class TestAutoResolveTrigger:

    def test_all_structured_submissions_trigger_auto_resolve(self):
        """All players submit attack → turn resolves automatically."""
        state = _make_party_state()
        enter_rounds(state)
        open_turn(state)

        npc = state.npcs_in_current_room[0]
        char_ids = list(state.characters.keys())

        # Place everyone in melee range
        for cid in char_ids:
            state.battlefield.combatants[cid].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE

        action = CombatAction(action_id="attack", target_id=npc.npc_id)
        submit_turn(state, char_ids[0], "Attack", combat_action=action.to_dict())
        result = submit_turn(state, char_ids[1], "Attack", combat_action=action.to_dict())

        # Second submission completes the round — should auto-resolve
        assert result.ok
        assert result.notify_dm is False
        assert result.auto_resolved is True        # platform should post narrative
        assert result.message != ""               # narrative is populated
        # The resolved round is in history; a new open round starts immediately
        assert state.current_turn is not None          # next round already open
        assert state.current_turn.status == TurnStatus.OPEN
        assert state.turn_number == 2              # counter advanced
        assert len(state.turn_history) == 1

    def test_auto_resolved_flag_false_for_partial_submission(self):
        """Partial submission (not all players) should not set auto_resolved."""
        state = _make_party_state()
        enter_rounds(state)
        open_turn(state)

        npc = state.npcs_in_current_room[0]
        char_ids = list(state.characters.keys())
        for cid in char_ids:
            state.battlefield.combatants[cid].range_band = RangeBand.ENGAGE

        action = CombatAction(action_id="attack", target_id=npc.npc_id)
        result = submit_turn(state, char_ids[0], "Attack", combat_action=action.to_dict())

        assert result.ok
        assert result.auto_resolved is False
        assert result.notify_dm is False

    def test_auto_resolved_flag_false_for_affect(self):
        """Affect submission (DM needed) should not set auto_resolved."""
        state = _make_party_state()
        enter_rounds(state)
        open_turn(state)

        npc = state.npcs_in_current_room[0]
        char_ids = list(state.characters.keys())
        for cid in char_ids:
            state.battlefield.combatants[cid].range_band = RangeBand.ENGAGE

        attack = CombatAction(action_id="attack", target_id=npc.npc_id)
        affect = CombatAction(action_id="affect",  free_text="I taunt the goblin.")

        submit_turn(state, char_ids[0], "Attack", combat_action=attack.to_dict())
        result = submit_turn(state, char_ids[1], "Taunt",  combat_action=affect.to_dict())

        assert result.ok
        assert result.auto_resolved is False
        assert result.notify_dm is True

    def test_affect_submission_suppresses_auto_resolve(self):
        """One Affect among submissions → DM resolution required."""
        state = _make_party_state()
        enter_rounds(state)
        open_turn(state)

        npc = state.npcs_in_current_room[0]
        char_ids = list(state.characters.keys())

        attack = CombatAction(action_id="attack", target_id=npc.npc_id)
        affect = CombatAction(action_id="affect",  free_text="I taunt the goblin.")

        submit_turn(state, char_ids[0], "Attack", combat_action=attack.to_dict())
        result = submit_turn(state, char_ids[1], "Taunt",  combat_action=affect.to_dict())

        assert result.ok
        assert result.notify_dm is True            # DM must resolve
        assert state.current_turn is not None      # still open/closed for DM
        assert state.current_turn.status == TurnStatus.CLOSED

    def test_exploration_mode_never_auto_resolves(self):
        """In exploration mode, all submissions close the turn for DM."""
        state = _make_party_state()  # stays in EXPLORATION
        char_ids = list(state.characters.keys())

        action = CombatAction(action_id="attack", target_id=uuid4())
        submit_turn(state, char_ids[0], "Search", combat_action=action.to_dict())
        result = submit_turn(state, char_ids[1], "Listen", combat_action=action.to_dict())

        assert result.notify_dm is True
        assert state.current_turn.status == TurnStatus.CLOSED

    def test_partial_submissions_do_not_resolve(self):
        """Only one of two players submitted — round stays open."""
        state = _make_party_state()
        enter_rounds(state)
        open_turn(state)

        npc = state.npcs_in_current_room[0]
        char_ids = list(state.characters.keys())
        action = CombatAction(action_id="attack", target_id=npc.npc_id)

        result = submit_turn(state, char_ids[0], "Attack", combat_action=action.to_dict())
        assert result.ok
        assert result.notify_dm is False
        assert state.current_turn is not None
        assert state.current_turn.status == TurnStatus.OPEN


# ---------------------------------------------------------------------------
# apply_condition
# ---------------------------------------------------------------------------

class TestApplyCondition:

    def test_apply_unknown_condition_fails(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        result = apply_condition(state, char_id, "nonexistent", duration=3)
        assert not result.ok
        assert "Unknown condition" in result.error

    def test_apply_outside_rounds_fails(self):
        state = _make_state_with_npc()
        # Don't enter rounds — no battlefield
        char_id = list(state.characters.keys())[0]
        result = apply_condition(state, char_id, "poisoned", duration=3)
        assert not result.ok

    def test_apply_to_unknown_combatant_fails(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        # Manually add condition to registry for this test
        from engine.data_loader import CONDITION_REGISTRY, ConditionDef
        CONDITION_REGISTRY["test_cond"] = ConditionDef(
            condition_id="test_cond", label="Test", duration_type="rounds", hooks={}
        )
        result = apply_condition(state, uuid4(), "test_cond", duration=1)
        assert not result.ok
        assert "not found" in result.error
        del CONDITION_REGISTRY["test_cond"]

    def test_apply_condition_adds_to_combatant(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]

        # Register a temporary test condition
        from engine.data_loader import CONDITION_REGISTRY, ConditionDef
        CONDITION_REGISTRY["slowed"] = ConditionDef(
            condition_id="slowed", label="Slowed", duration_type="rounds", hooks={}
        )

        result = apply_condition(state, char_id, "slowed", duration=2)
        assert result.ok
        cs = state.battlefield.combatants[char_id]
        assert any(c.condition_id == "slowed" for c in cs.active_conditions)
        del CONDITION_REGISTRY["slowed"]

    def test_reapply_refreshes_duration(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]

        from engine.data_loader import CONDITION_REGISTRY, ConditionDef
        CONDITION_REGISTRY["burning"] = ConditionDef(
            condition_id="burning", label="Burning", duration_type="rounds", hooks={}
        )

        apply_condition(state, char_id, "burning", duration=1)
        apply_condition(state, char_id, "burning", duration=5)
        cs = state.battlefield.combatants[char_id]
        conds = [c for c in cs.active_conditions if c.condition_id == "burning"]
        assert len(conds) == 1
        assert conds[0].duration_rounds == 5
        del CONDITION_REGISTRY["burning"]


# ---------------------------------------------------------------------------
# _tick_conditions
# ---------------------------------------------------------------------------

class TestTickConditions:

    def test_duration_decrements(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        cs = state.battlefield.combatants[char_id]
        cs.active_conditions = [ActiveCondition(condition_id="x", duration_rounds=3)]

        _tick_conditions(state, [])
        assert cs.active_conditions[0].duration_rounds == 2

    def test_condition_expires_at_zero(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        cs = state.battlefield.combatants[char_id]
        cs.active_conditions = [ActiveCondition(condition_id="x", duration_rounds=1)]

        log: list[str] = []
        _tick_conditions(state, log)
        assert cs.active_conditions == []

    def test_permanent_condition_never_expires(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        cs = state.battlefield.combatants[char_id]
        cs.active_conditions = [ActiveCondition(condition_id="x", duration_rounds=None)]

        for _ in range(10):
            _tick_conditions(state, [])
        assert len(cs.active_conditions) == 1


# ---------------------------------------------------------------------------
# NPC AI
# ---------------------------------------------------------------------------

class TestNPCDecide:

    def test_npc_at_far_moves_toward_engage(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        npc = state.npcs_in_current_room[0]
        cs = state.battlefield.combatants[npc.npc_id]
        cs.range_band = RangeBand.FAR_PLUS

        action = _npc_decide(state, npc.npc_id, cs)
        assert action is not None
        assert action.action_id == "move"
        # Should step from FAR_PLUS toward ENGAGE (i.e. to CLOSE_PLUS)
        assert action.destination == RangeBand.CLOSE_PLUS

    def test_npc_at_engage_attacks(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        npc = state.npcs_in_current_room[0]
        cs = state.battlefield.combatants[npc.npc_id]
        cs.range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id is not None

    def test_npc_targets_lowest_hp_player(self):
        state = _make_party_state()
        enter_rounds(state)
        npc = state.npcs_in_current_room[0]
        cs  = state.battlefield.combatants[npc.npc_id]
        cs.range_band = RangeBand.ENGAGE

        chars = list(state.characters.values())
        chars[0].hp_current = 1
        chars[1].hp_current = 8
        # Place chars in range
        for c in chars:
            state.battlefield.combatants[c.character_id].range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, cs)
        assert action.target_id == chars[0].character_id

    def test_npc_no_action_when_no_players(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        npc = state.npcs_in_current_room[0]
        cs  = state.battlefield.combatants[npc.npc_id]
        cs.range_band = RangeBand.ENGAGE

        # Kill all player characters
        from models import CharacterStatus
        for char in state.characters.values():
            char.status = CharacterStatus.DEAD
            state.battlefield.combatants.pop(char.character_id, None)

        action = _npc_decide(state, npc.npc_id, cs)
        assert action is None


# ---------------------------------------------------------------------------
# Serialization after combat
# ---------------------------------------------------------------------------

class TestCombatSerialization:

    def test_battlefield_survives_round_trip(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        open_turn(state)

        char_id = list(state.characters.keys())[0]
        npc = state.npcs_in_current_room[0]

        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=npc.npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]
        auto_resolve_round(state)

        j = serialize_state(state)
        state2 = deserialize_state(j)

        assert state2.battlefield is not None
        assert state2.mode == SessionMode.ROUNDS

    def test_auto_resolved_turn_in_history(self):
        state = _make_party_state()
        enter_rounds(state)
        open_turn(state)

        npc = state.npcs_in_current_room[0]
        char_ids = list(state.characters.keys())
        for cid in char_ids:
            state.battlefield.combatants[cid].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE

        action = CombatAction(action_id="attack", target_id=npc.npc_id)
        submit_turn(state, char_ids[0], "Attack", combat_action=action.to_dict())
        submit_turn(state, char_ids[1], "Attack", combat_action=action.to_dict())

        j = serialize_state(state)
        state2 = deserialize_state(j)
        assert len(state2.turn_history) == 1
        assert state2.turn_history[0].status.value == "resolved"
        assert len(state2.turn_history[0].resolution) > 0


# ---------------------------------------------------------------------------
# Phase 4 — Status conditions
# ---------------------------------------------------------------------------

class TestConditions:
    """
    Tests for the four Phase 4 conditions: poisoned, stunned, strengthened,
    entangled.  All four are real data files loaded from disk, so these tests
    also serve as integration checks for the data → hook → engine pipeline.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _state_in_rounds(self):
        """One-character combat state, both combatants at ENGAGE."""
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        npc = state.npcs_in_current_room[0]
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE
        # Give character enough HP to survive condition damage
        state.characters[char_id].hp_current = 20
        state.characters[char_id].hp_max = 20
        # Give NPC enough HP to survive attacks
        npc.hp_current = 20
        npc.hp_max = 20
        return state, char_id, npc

    # ------------------------------------------------------------------
    # Condition registry sanity
    # ------------------------------------------------------------------

    def test_all_four_conditions_loaded(self):
        from engine import CONDITION_REGISTRY
        for cid in ("poisoned", "stunned", "strengthened", "entangled"):
            assert cid in CONDITION_REGISTRY, f"'{cid}' not in CONDITION_REGISTRY"

    def test_poisoned_has_on_turn_end_hook(self):
        from engine import CONDITION_REGISTRY
        entry = CONDITION_REGISTRY["poisoned"].hooks.get("on_turn_end")
        # Now a hook object, not a plain string
        assert isinstance(entry, dict)
        assert entry["tag"] == "deal_damage"
        assert entry["dice"] == "1d4"
        assert entry["type"] == "poison"

    def test_stunned_has_on_turn_start_hook(self):
        from engine import CONDITION_REGISTRY
        # Plain string — no params needed
        assert CONDITION_REGISTRY["stunned"].hooks.get("on_turn_start") == "skip_action"

    def test_strengthened_has_str_modifier(self):
        from engine import CONDITION_REGISTRY
        assert CONDITION_REGISTRY["strengthened"].stat_modifiers.get("physique") == 2

    def test_entangled_has_on_move_hook(self):
        from engine import CONDITION_REGISTRY
        assert CONDITION_REGISTRY["entangled"].hooks.get("on_move") == "block_movement"

    # ------------------------------------------------------------------
    # apply_condition with real conditions
    # ------------------------------------------------------------------

    def test_apply_poisoned(self):
        state, char_id, _ = self._state_in_rounds()
        result = apply_condition(state, char_id, "poisoned", duration=3)
        assert result.ok
        cs = state.battlefield.combatants[char_id]
        assert any(c.condition_id == "poisoned" for c in cs.active_conditions)

    def test_apply_condition_message_contains_label(self):
        state, char_id, _ = self._state_in_rounds()
        result = apply_condition(state, char_id, "stunned", duration=1)
        assert result.ok
        assert "Stunned" in result.message

    # ------------------------------------------------------------------
    # Poisoned — deals 1d4 damage on_turn_end
    # ------------------------------------------------------------------

    def test_poisoned_deals_damage_each_round(self):
        state, char_id, npc = self._state_in_rounds()
        apply_condition(state, char_id, "poisoned", duration=3)
        hp_before = state.characters[char_id].hp_current

        # Manually trigger _tick_conditions (simulates end of round)
        from engine.combat import _tick_conditions
        log: list[str] = []
        _tick_conditions(state, log)

        hp_after = state.characters[char_id].hp_current
        assert hp_after < hp_before, "Poisoned character should have lost HP"
        assert any("poison" in entry for entry in log)

    def test_poisoned_damage_is_1_to_4(self):
        """Run many ticks; all damage values must fall in [1, 4]."""
        from engine.combat import _tick_conditions
        damages = set()
        for _ in range(60):
            state, char_id, _ = self._state_in_rounds()
            apply_condition(state, char_id, "poisoned", duration=5)
            hp_before = state.characters[char_id].hp_current
            _tick_conditions(state, [])
            damage = hp_before - state.characters[char_id].hp_current
            if damage > 0:
                damages.add(damage)
        assert damages <= {1, 2, 3, 4}

    def test_poisoned_expires_after_duration(self):
        state, char_id, _ = self._state_in_rounds()
        apply_condition(state, char_id, "poisoned", duration=2)
        from engine.combat import _tick_conditions
        _tick_conditions(state, [])   # round 1 — duration becomes 1
        _tick_conditions(state, [])   # round 2 — expires
        cs = state.battlefield.combatants[char_id]
        assert not any(c.condition_id == "poisoned" for c in cs.active_conditions)

    # ------------------------------------------------------------------
    # Stunned — skip_action for one round
    # ------------------------------------------------------------------

    def test_stunned_sets_skip_action_flag(self):
        state, char_id, _ = self._state_in_rounds()
        apply_condition(state, char_id, "stunned", duration=1)
        from engine.combat import _fire_turn_start_hooks
        log: list[str] = []
        _fire_turn_start_hooks(state, log)
        cs = state.battlefield.combatants[char_id]
        assert cs.skip_action is True

    def test_stunned_character_skips_action_in_round(self):
        state, char_id, npc = self._state_in_rounds()
        open_turn(state)
        apply_condition(state, char_id, "stunned", duration=1)

        action = CombatAction(action_id="attack", target_id=npc.npc_id)
        from models import PlayerTurnSubmission
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]
        result = auto_resolve_round(state)
        assert result.ok
        assert "stunned" in result.message.lower()
        # NPC should be unharmed since player was stunned
        assert npc.hp_current == 20

    def test_skip_action_flag_cleared_after_round(self):
        state, char_id, _ = self._state_in_rounds()
        apply_condition(state, char_id, "stunned", duration=2)
        open_turn(state)

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=state.npcs_in_current_room[0].npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]
        auto_resolve_round(state)
        cs = state.battlefield.combatants.get(char_id)
        if cs:
            assert cs.skip_action is False

    # ------------------------------------------------------------------
    # Strengthened — +2 STR modifier to attacks
    # ------------------------------------------------------------------

    def test_strengthened_increases_effective_str_mod(self):
        from engine.combat import _effective_stat_mod
        state, char_id, _ = self._state_in_rounds()

        char = state.characters[char_id]
        char.ability_scores.physique = 0   # zero base stat
        base_mod = _effective_stat_mod(state, char_id, "physique")
        assert base_mod == 0

        # strengthened condition adds 2 directly to the stat (pass-through model)
        apply_condition(state, char_id, "strengthened", duration=3)
        boosted_mod = _effective_stat_mod(state, char_id, "physique")
        assert boosted_mod == 2

    def test_strengthened_stacks_with_base_strength(self):
        from engine.combat import _effective_stat_mod
        state, char_id, _ = self._state_in_rounds()

        char = state.characters[char_id]
        char.ability_scores.physique = 200   # base stat of 200
        apply_condition(state, char_id, "strengthened", duration=3)
        # 200 base + 2 condition bonus = 202
        assert _effective_stat_mod(state, char_id, "physique") == 202

    def test_strengthened_has_no_hooks(self):
        from engine import CONDITION_REGISTRY
        cond = CONDITION_REGISTRY["strengthened"]
        assert not cond.hooks  # empty dict — purely stat-modifier based

    # ------------------------------------------------------------------
    # Entangled — cannot move
    # ------------------------------------------------------------------

    def test_entangled_blocks_movement(self):
        state, char_id, _ = self._state_in_rounds()
        apply_condition(state, char_id, "entangled", duration=2)
        open_turn(state)

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="move", destination=RangeBand.FAR_MINUS)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Move",
            is_latest=True, combat_action=action.to_dict(),
        )]
        result = auto_resolve_round(state)
        assert result.ok
        assert "entangled" in result.message.lower()
        # Character should still be at ENGAGE (didn't move)
        cs = state.battlefield.combatants.get(char_id)
        if cs:
            assert cs.range_band == RangeBand.ENGAGE

    def test_entangled_does_not_block_attack(self):
        """Entangled only prevents movement; attacks are unaffected."""
        state, char_id, npc = self._state_in_rounds()
        apply_condition(state, char_id, "entangled", duration=2)
        open_turn(state)

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="attack", target_id=npc.npc_id)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Attack",
            is_latest=True, combat_action=action.to_dict(),
        )]
        result = auto_resolve_round(state)
        assert result.ok
        # Attack should have resolved (NPC may or may not have taken damage
        # depending on roll, but no movement-related error)
        assert "cannot move" not in result.message.lower() or "attacks" in result.message.lower()

    def test_movement_blocked_flag_cleared_after_round(self):
        state, char_id, _ = self._state_in_rounds()
        apply_condition(state, char_id, "entangled", duration=3)
        open_turn(state)

        from models import PlayerTurnSubmission
        action = CombatAction(action_id="move", destination=RangeBand.FAR_MINUS)
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Move",
            is_latest=True, combat_action=action.to_dict(),
        )]
        auto_resolve_round(state)
        cs = state.battlefield.combatants.get(char_id)
        if cs:
            assert cs.movement_blocked is False


# ---------------------------------------------------------------------------
# Poison action (thief-exclusive)
# ---------------------------------------------------------------------------

class TestPoisonAction:

    def _thief_state(self):
        """Thief + NPC in ROUNDS mode."""
        from engine import add_npc, register_room
        from models import Room
        state = GameState(platform_channel_id="ch", dm_user_id="dm")
        state.party = Party(name="P")
        create_character(state, "Rogue", CharacterClass.THIEF, "Pack A", owner_id="u1")
        start_session(state)
        room = Room(name="Hall", description="Hall.")
        register_room(state, room)
        state.current_room_id = room.room_id
        npc = NPC(name="Guard", hp_current=20, hp_max=20, armor_class=5)
        add_npc(state, npc)
        enter_rounds(state)
        open_turn(state)
        # Give thief enough HP to survive any NPC counter-attack
        char_id = list(state.characters.keys())[0]
        state.characters[char_id].hp_current = 20
        state.characters[char_id].hp_max = 20
        return state, list(state.characters.keys())[0], npc

    def test_poison_in_thief_actions(self):
        from engine import CLASS_DEFINITIONS
        actions = CLASS_DEFINITIONS["THIEF"].combat_actions
        assert "poison" in actions
        assert actions.index("poison") < actions.index("affect")

    def test_poison_not_in_fighter_actions(self):
        from engine import CLASS_DEFINITIONS
        assert "poison" not in CLASS_DEFINITIONS["KNIGHT"].combat_actions

    def test_poison_has_no_range_requirement(self):
        from engine import ACTION_REGISTRY
        assert ACTION_REGISTRY["poison"].range_requirement == []

    def test_poison_requires_target(self):
        from engine import ACTION_REGISTRY
        assert ACTION_REGISTRY["poison"].requires_target is True

    def test_poison_applies_condition_to_target(self):
        state, char_id, npc = self._thief_state()
        action = CombatAction(action_id="poison", target_id=npc.npc_id)
        from models import PlayerTurnSubmission
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Poison",
            is_latest=True, combat_action=action.to_dict(),
        )]
        result = auto_resolve_round(state)
        assert result.ok
        npc_cs = state.battlefield.combatants.get(npc.npc_id)
        assert npc_cs is not None
        assert any(c.condition_id == "poisoned" for c in npc_cs.active_conditions)

    def test_poison_works_at_any_range(self):
        """Thief at FAR_MINUS, guard at FAR_PLUS — should still apply."""
        state, char_id, npc = self._thief_state()
        action = CombatAction(action_id="poison", target_id=npc.npc_id)
        from models import PlayerTurnSubmission
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Poison",
            is_latest=True, combat_action=action.to_dict(),
        )]
        result = auto_resolve_round(state)
        assert result.ok
        # New log format: "Rogue applies Poisoned to Guard! (3 rounds)"
        assert "applies" in result.message.lower() or "poison" in result.message.lower()

    def test_poison_tick_fires_same_round_applied(self):
        """Condition is applied mid-round; _tick_conditions runs at end so
        first damage tick happens immediately — duration decrements to 2."""
        state, char_id, npc = self._thief_state()
        action = CombatAction(action_id="poison", target_id=npc.npc_id)
        from models import PlayerTurnSubmission
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Poison",
            is_latest=True, combat_action=action.to_dict(),
        )]
        auto_resolve_round(state)
        npc_cs = state.battlefield.combatants.get(npc.npc_id)
        assert npc_cs is not None
        cond = next(c for c in npc_cs.active_conditions if c.condition_id == "poisoned")
        assert cond.duration_rounds == 2   # started at 3, ticked once
        assert npc.hp_current < 20         # took poison damage this round

    def test_poison_narrative_mentions_target(self):
        state, char_id, npc = self._thief_state()
        action = CombatAction(action_id="poison", target_id=npc.npc_id)
        from models import PlayerTurnSubmission
        state.current_turn.submissions = [PlayerTurnSubmission(
            character_id=char_id, action_text="Poison",
            is_latest=True, combat_action=action.to_dict(),
        )]
        result = auto_resolve_round(state)
        assert "Guard" in result.message
        assert "Rogue" in result.message


# ---------------------------------------------------------------------------
# Parameterized hook system
# ---------------------------------------------------------------------------

class TestParameterizedHooks:
    """
    Tests for the parameterized hook dispatch system:
      - _dispatch_hook handles plain strings and hook objects identically
      - deal_damage uses dice/type params
      - melee_attack uses dice param
      - apply_condition uses condition/duration params
      - unknown tags log a warning without raising
    """

    def _state_with_char_and_npc(self):
        state = _make_state_with_npc()
        enter_rounds(state)
        char_id = list(state.characters.keys())[0]
        npc = state.npcs_in_current_room[0]
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE
        state.characters[char_id].hp_current = 20
        state.characters[char_id].hp_max = 20
        npc.hp_current = 20
        npc.hp_max = 20
        return state, char_id, npc

    # ------------------------------------------------------------------
    # _dispatch_hook — core dispatch logic
    # ------------------------------------------------------------------

    def test_plain_string_tag_dispatches(self):
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        cs = state.battlefield.combatants[char_id]
        assert cs.skip_action is False
        log: list[str] = []
        _dispatch_hook("skip_action", state, char_id, None, log)
        assert cs.skip_action is True

    def test_hook_object_dispatches(self):
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        hp_before = state.characters[char_id].hp_current
        log: list[str] = []
        _dispatch_hook({"tag": "deal_damage", "dice": "1d4", "type": "fire"}, state, char_id, None, log)
        assert state.characters[char_id].hp_current < hp_before
        assert any("fire" in e for e in log)

    def test_unknown_tag_logs_warning_does_not_raise(self):
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        log: list[str] = []
        _dispatch_hook("completely_unknown_tag", state, char_id, None, log)
        assert any("unknown hook tag" in e for e in log)

    def test_hook_object_missing_tag_logs_warning(self):
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        log: list[str] = []
        _dispatch_hook({"dice": "1d6"}, state, char_id, None, log)  # missing "tag"
        assert any("empty tag" in e or "unknown" in e for e in log)

    # ------------------------------------------------------------------
    # deal_damage — dice and type params
    # ------------------------------------------------------------------

    def test_deal_damage_default_dice(self):
        """Default dice is 1d6 when not specified."""
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        damages = set()
        for _ in range(40):
            s2, cid, _ = self._state_with_char_and_npc()
            hp_before = s2.characters[cid].hp_current
            _dispatch_hook({"tag": "deal_damage"}, s2, cid, None, [])
            d = hp_before - s2.characters[cid].hp_current
            if d > 0:
                damages.add(d)
        assert damages <= {1, 2, 3, 4, 5, 6}

    def test_deal_damage_custom_dice(self):
        """dice param controls the roll range."""
        from engine.combat import _dispatch_hook
        damages = set()
        for _ in range(60):
            state, char_id, _ = self._state_with_char_and_npc()
            hp_before = state.characters[char_id].hp_current
            _dispatch_hook({"tag": "deal_damage", "dice": "1d4"}, state, char_id, None, [])
            d = hp_before - state.characters[char_id].hp_current
            if d > 0:
                damages.add(d)
        assert damages <= {1, 2, 3, 4}

    def test_deal_damage_type_in_log(self):
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        log: list[str] = []
        _dispatch_hook({"tag": "deal_damage", "dice": "1d4", "type": "necrotic"}, state, char_id, None, log)
        assert any("necrotic" in e for e in log)

    # ------------------------------------------------------------------
    # melee_attack — dice param
    # ------------------------------------------------------------------

    def test_melee_attack_uses_dice_param(self):
        """A d20 weapon should produce damage in [1, 20] range over many rolls."""
        from engine.combat import _dispatch_hook
        damages = set()
        for _ in range(80):
            state, char_id, npc = self._state_with_char_and_npc()
            npc.armor_class = 1  # guarantee hit
            npc.hp_current = 100
            action = CombatAction(action_id="attack", target_id=npc.npc_id)
            log: list[str] = []
            _dispatch_hook({"tag": "melee_attack", "dice": "1d20"}, state, char_id, action, log)
            if any("hits" in e for e in log):
                import re
                for e in log:
                    m = re.search(r"Deals (\d+) damage", e)
                    if m:
                        damages.add(int(m.group(1)))
        # With 1d20 and any str modifier, max damage ≥ 1 and we should see
        # values beyond what 1d6 can produce (> 6) over 80 rolls
        assert any(d > 6 for d in damages), f"Expected d20 damage > 6, got {damages}"

    # ------------------------------------------------------------------
    # apply_condition — condition/duration params
    # ------------------------------------------------------------------

    def test_apply_condition_hook_applies_named_condition(self):
        from engine.combat import _dispatch_hook
        state, char_id, npc = self._state_with_char_and_npc()
        action = CombatAction(action_id="poison", target_id=npc.npc_id)
        log: list[str] = []
        _dispatch_hook(
            {"tag": "apply_condition", "condition": "stunned", "duration": 2},
            state, char_id, action, log,
        )
        npc_cs = state.battlefield.combatants[npc.npc_id]
        assert any(c.condition_id == "stunned" for c in npc_cs.active_conditions)
        stunned = next(c for c in npc_cs.active_conditions if c.condition_id == "stunned")
        assert stunned.duration_rounds == 2

    def test_apply_condition_hook_missing_condition_param_logs_error(self):
        from engine.combat import _dispatch_hook
        state, char_id, npc = self._state_with_char_and_npc()
        action = CombatAction(action_id="poison", target_id=npc.npc_id)
        log: list[str] = []
        _dispatch_hook({"tag": "apply_condition"}, state, char_id, action, log)
        assert any("condition" in e.lower() for e in log)

    def test_apply_condition_hook_no_target_logs_error(self):
        from engine.combat import _dispatch_hook
        state, char_id, _ = self._state_with_char_and_npc()
        log: list[str] = []
        _dispatch_hook(
            {"tag": "apply_condition", "condition": "stunned"},
            state, char_id, None, log,  # action=None, no target
        )
        assert any("no target" in e.lower() for e in log)


# ---------------------------------------------------------------------------
# data_loader: hook object validation
# ---------------------------------------------------------------------------

class TestHookObjectValidation:
    """Tests that data_loader correctly validates hook objects in data files."""

    def test_plain_string_hook_loads(self):
        import json
        import tempfile
        from pathlib import Path

        from engine.data_loader import load_all
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "actions").mkdir()
            (p / "conditions").mkdir()
            (p / "classes").mkdir()
            (p / "conditions" / "test.json").write_text(json.dumps({
                "condition_id": "test", "label": "Test", "duration_type": "rounds",
                "hooks": {"on_turn_end": "skip_action"},
            }))
            _, cr, _, _ = load_all(p)
            assert cr["test"].hooks["on_turn_end"] == "skip_action"

    def test_hook_object_loads(self):
        import json
        import tempfile
        from pathlib import Path

        from engine.data_loader import load_all
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "actions").mkdir()
            (p / "conditions").mkdir()
            (p / "classes").mkdir()
            (p / "conditions" / "burning.json").write_text(json.dumps({
                "condition_id": "burning", "label": "Burning", "duration_type": "rounds",
                "hooks": {"on_turn_end": {"tag": "deal_damage", "dice": "1d6", "type": "fire"}},
            }))
            _, cr, _, _ = load_all(p)
            entry = cr["burning"].hooks["on_turn_end"]
            assert isinstance(entry, dict)
            assert entry["tag"] == "deal_damage"
            assert entry["dice"] == "1d6"

    def test_hook_object_missing_tag_raises(self):
        import json
        import tempfile
        from pathlib import Path

        from engine.data_loader import load_all
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "actions").mkdir()
            (p / "conditions").mkdir()
            (p / "classes").mkdir()
            (p / "conditions" / "bad.json").write_text(json.dumps({
                "condition_id": "bad", "label": "Bad", "duration_type": "rounds",
                "hooks": {"on_turn_end": {"dice": "1d6"}},   # missing "tag"
            }))
            try:
                load_all(p)
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "tag" in str(e).lower()

    def test_hook_object_in_effect_tags_loads(self):
        import json
        import tempfile
        from pathlib import Path

        from engine.data_loader import load_all
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "actions").mkdir()
            (p / "conditions").mkdir()
            (p / "classes").mkdir()
            (p / "actions" / "stab.json").write_text(json.dumps({
                "action_id": "stab", "label": "Stab", "button_style": "danger",
                "action_type": "attack", "requires_target": True,
                "requires_destination": False, "range_requirement": [],
                "effect_tags": [{"tag": "melee_attack", "dice": "1d4"}, "check_death"],
            }))
            ar, _, _, _ = load_all(p)
            tags = ar["stab"].effect_tags
            assert tags[0] == {"tag": "melee_attack", "dice": "1d4"}
            assert tags[1] == "check_death"

    def test_effect_tag_object_missing_tag_raises(self):
        import json
        import tempfile
        from pathlib import Path

        from engine.data_loader import load_all
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "actions").mkdir()
            (p / "conditions").mkdir()
            (p / "classes").mkdir()
            (p / "actions" / "bad.json").write_text(json.dumps({
                "action_id": "bad", "label": "Bad", "button_style": "danger",
                "action_type": "attack", "requires_target": False,
                "requires_destination": False, "range_requirement": [],
                "effect_tags": [{"dice": "1d6"}],   # missing "tag"
            }))
            try:
                load_all(p)
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "tag" in str(e).lower()
