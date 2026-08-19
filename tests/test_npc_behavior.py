"""
tests/test_npc_behavior.py — NPC behavior-mode AI tests.
"""

from __future__ import annotations

from engine import (
    add_npc,
    apply_condition,
    create_character,
    enter_rounds,
    register_room,
    start_session,
)
from engine.combat import _npc_decide
from engine.data_loader import CharacterClass
from models import (
    NPC,
    GameState,
    NPCBehaviorMode,
    Party,
    RangeBand,
    Room,
)
from serialization import deserialize_npc, serialize_npc


def _make_state(num_chars: int = 1) -> GameState:
    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    for i in range(num_chars):
        create_character(
            state, f"Hero{i + 1}",
            CharacterClass.KNIGHT if i == 0 else CharacterClass.MAGE,
            "Pack A",
            owner_id=f"u{i + 1}",
        )
    start_session(state)
    room = Room(name="Hall", description="A stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id
    return state


class TestSimpleBehavior:
    def test_attacks_random_player_in_range(self, monkeypatch):
        state = _make_state(num_chars=2)
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SIMPLE)
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        for c in chars:
            state.battlefield.combatants[c.character_id].range_band = RangeBand.ENGAGE
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        # Force the random pick to the second player.
        monkeypatch.setattr("engine.combat.random.choice", lambda seq: seq[1])
        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == chars[1].character_id

    def test_moves_toward_random_target_when_out_of_range(self, monkeypatch):
        state = _make_state(num_chars=2)
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SIMPLE)
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        # Force target selection to the second player.
        monkeypatch.setattr("engine.combat.random.choice", lambda seq: seq[1])
        state.battlefield.combatants[chars[1].character_id].range_band = RangeBand.FAR_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.FAR_PLUS

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "move"
        assert action.destination == RangeBand.CLOSE_PLUS

    def test_attacks_target_in_shared_band_even_if_random_choice_is_far(self, monkeypatch):
        state = _make_state(num_chars=2)
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SIMPLE)
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        # NPC shares a band with chars[0]; chars[1] is far away.
        state.battlefield.combatants[chars[0].character_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[chars[1].character_id].range_band = RangeBand.FAR_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        # If given the full list, choose the far target; if given only the
        # same-band target(s), choose that one. This proves the NPC filters to
        # its current band before picking randomly.
        def _choice(seq):
            return seq[0] if len(seq) == 1 else seq[1]

        monkeypatch.setattr("engine.combat.random.choice", _choice)
        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == chars[0].character_id


class TestSmartBehavior:
    def test_attacks_lowest_hp_player_in_range(self):
        state = _make_state(num_chars=2)
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SMART)
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        chars[0].hp_current = 10
        chars[1].hp_current = 3
        for c in chars:
            state.battlefield.combatants[c.character_id].range_band = RangeBand.ENGAGE
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == chars[1].character_id

    def test_ignores_lower_hp_player_out_of_range(self):
        state = _make_state(num_chars=2)
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SMART)
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        # Char 0 is in melee range; char 1 is farther but lower HP.
        chars[0].hp_current = 10
        chars[1].hp_current = 1
        state.battlefield.combatants[chars[0].character_id].range_band = RangeBand.ENGAGE
        state.battlefield.combatants[chars[1].character_id].range_band = RangeBand.CLOSE_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == chars[0].character_id

    def test_moves_toward_lowest_hp_when_none_in_range(self):
        state = _make_state(num_chars=2)
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SMART)
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        chars[0].hp_current = 8
        chars[1].hp_current = 2
        state.battlefield.combatants[chars[0].character_id].range_band = RangeBand.FAR_MINUS
        state.battlefield.combatants[chars[1].character_id].range_band = RangeBand.FAR_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.FAR_PLUS

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "move"
        assert action.destination == RangeBand.CLOSE_PLUS

    def test_attacks_shared_band_enemy_when_target_is_far(self):
        state = _make_state(num_chars=2)
        npc = NPC(
            name="Goblin", hp_current=5, hp_max=5,
            weapon_range=0, behavior=NPCBehaviorMode.SMART,
        )
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        # Char 0 is hidden but shares the band; char 1 is farther and lower HP.
        state.battlefield.combatants[chars[0].character_id].range_band = RangeBand.ENGAGE
        apply_condition(state, chars[0].character_id, "hidden")
        chars[0].hp_current = 100
        chars[1].hp_current = 1
        state.battlefield.combatants[chars[1].character_id].range_band = RangeBand.FAR_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == chars[0].character_id


class TestRangedBehavior:
    def test_attacks_in_range(self):
        state = _make_state(num_chars=1)
        npc = NPC(
            name="Archer", hp_current=5, hp_max=5,
            weapon_range=2, behavior=NPCBehaviorMode.RANGED,
        )
        add_npc(state, npc)
        enter_rounds(state)

        char_id = next(iter(state.characters))
        state.battlefield.combatants[char_id].range_band = RangeBand.CLOSE_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.CLOSE_PLUS

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == char_id

    def test_retreats_when_engaged(self):
        state = _make_state(num_chars=1)
        npc = NPC(
            name="Archer", hp_current=5, hp_max=5,
            weapon_range=2, behavior=NPCBehaviorMode.RANGED,
        )
        add_npc(state, npc)
        enter_rounds(state)

        char_id = next(iter(state.characters))
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "move"
        assert action.destination == RangeBand.CLOSE_PLUS

    def test_advances_when_too_far(self):
        state = _make_state(num_chars=1)
        npc = NPC(
            name="Archer", hp_current=5, hp_max=5,
            weapon_range=2, behavior=NPCBehaviorMode.RANGED,
        )
        add_npc(state, npc)
        enter_rounds(state)

        char_id = next(iter(state.characters))
        state.battlefield.combatants[char_id].range_band = RangeBand.FAR_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.FAR_PLUS

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "move"
        assert action.destination == RangeBand.CLOSE_PLUS

    def test_holds_at_max_range(self):
        state = _make_state(num_chars=1)
        npc = NPC(
            name="Archer", hp_current=5, hp_max=5,
            weapon_range=2, behavior=NPCBehaviorMode.RANGED,
        )
        add_npc(state, npc)
        enter_rounds(state)

        char_id = next(iter(state.characters))
        state.battlefield.combatants[char_id].range_band = RangeBand.CLOSE_MINUS
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.CLOSE_PLUS

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"
        assert action.target_id == char_id

    def test_zero_range_falls_back_to_simple(self, monkeypatch):
        state = _make_state(num_chars=2)
        npc = NPC(
            name="Broken Archer", hp_current=5, hp_max=5,
            weapon_range=0, behavior=NPCBehaviorMode.RANGED,
        )
        add_npc(state, npc)
        enter_rounds(state)

        chars = list(state.characters.values())
        monkeypatch.setattr("engine.combat.random.choice", lambda seq: seq[0])
        for c in chars:
            state.battlefield.combatants[c.character_id].range_band = RangeBand.ENGAGE
        npc_cs = state.battlefield.combatants[npc.npc_id]
        npc_cs.range_band = RangeBand.ENGAGE

        action = _npc_decide(state, npc.npc_id, npc_cs)
        assert action is not None
        assert action.action_id == "attack"


class TestBehaviorSerialization:
    def test_behavior_round_trips(self):
        npc = NPC(name="Goblin", hp_current=5, hp_max=5, behavior=NPCBehaviorMode.SMART)
        data = serialize_npc(npc)
        restored = deserialize_npc(data)
        assert restored.behavior == NPCBehaviorMode.SMART

    def test_missing_behavior_defaults_to_simple(self):
        npc = NPC(name="Goblin", hp_current=5, hp_max=5)
        data = serialize_npc(npc)
        del data["behavior"]
        restored = deserialize_npc(data)
        assert restored.behavior == NPCBehaviorMode.SIMPLE
