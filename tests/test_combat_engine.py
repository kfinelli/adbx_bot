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
from tables import CharacterClass

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state_with_npc() -> GameState:
    """Active EXPLORATION state with one Fighter and one NPC in the current room."""
    from engine import add_npc, register_room
    from models import Room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Aldric", CharacterClass.FIGHTER, "Pack A", owner_id="u1")
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
    create_character(state, "Aldric", CharacterClass.FIGHTER, "Pack A", owner_id="u1")
    create_character(state, "Tomas",  CharacterClass.CLERIC,  "Pack A", owner_id="u2")
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
        """Enter rounds, open a turn, return (state, char_id, npc_id)."""
        state = _make_state_with_npc()
        npc = state.npcs_in_current_room[0]
        npc.hp_current = npc_hp
        npc.hp_max = npc_hp
        npc.armor_class = npc_ac
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
        """AC=20 makes hitting impossible (max roll on d20 = 20, needs >= 20)."""
        state, char_id, npc_id = self._setup_combat(npc_hp=5, npc_ac=20)
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
