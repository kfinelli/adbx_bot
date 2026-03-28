"""
tests/test_xp_sources.py — XP award sources: distribute_xp, NPC kills, room exploration.

Covers:
  - distribute_xp: even split, floor division, inactive chars excluded, level-ups
  - NPC kill XP: hit_dice * 100 split among active party via combat hooks
  - Room exploration XP: first-visit award via move_party_to_room / set_room
  - Serialization: hit_dice and exploration_xp round-trips and migration defaults

NOTE: No discord imports. All tests are CI-safe.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    add_npc,
    auto_resolve_round,
    create_character,
    distribute_xp,
    enter_rounds,
    move_party_to_room,
    open_turn,
    register_room,
    set_room,
    start_session,
)
from engine.azure_constants import DEFAULT_ROOM_XP
from engine.azure_engine import CharacterClass
from engine.combat import CombatAction
from models import (
    NPC,
    CharacterStatus,
    GameState,
    Party,
    PlayerTurnSubmission,
    RangeBand,
    Room,
)
from serialization import (
    deserialize_npc,
    deserialize_room,
    serialize_npc,
    serialize_room,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_state() -> GameState:
    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    return state


def _make_active_state(n_chars: int = 1) -> GameState:
    """EXPLORATION session with n_chars KNIGHT characters."""
    state = _make_state()
    for i in range(n_chars):
        create_character(state, f"Hero{i}", CharacterClass.KNIGHT, "", owner_id=f"u{i}")
    start_session(state)
    return state


def _make_combat_state(hit_dice: int = 1, n_chars: int = 1):
    """
    ROUNDS state with n_chars characters and one NPC at ENGAGE range.
    NPC has 1 HP and DEF=0 so any attack kills it.
    Returns (state, list_of_char_ids, npc_id).
    """
    state = _make_active_state(n_chars=n_chars)
    room = Room(name="Hall", description="A dark hall.")
    register_room(state, room)
    state.current_room_id = room.room_id
    npc = NPC(
        name="Goblin", hp_current=1, hp_max=1,
        defense=0, damage_dice="1d4", hit_dice=hit_dice,
    )
    add_npc(state, npc)
    # Give all characters enough HP to survive an NPC counter-attack
    for char in state.characters.values():
        char.hp_current = 50
        char.hp_max = 50
    enter_rounds(state)
    open_turn(state)
    # Place everyone in melee range
    for char_id in state.characters:
        state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
    state.battlefield.combatants[npc.npc_id].range_band = RangeBand.ENGAGE
    return state, list(state.characters.keys()), npc.npc_id


def _attack_submission(char_id, npc_id) -> PlayerTurnSubmission:
    action = CombatAction(action_id="attack", target_id=npc_id)
    return PlayerTurnSubmission(
        character_id=char_id,
        action_text="Attack",
        is_latest=True,
        combat_action=action.to_dict(),
    )


# ---------------------------------------------------------------------------
# distribute_xp
# ---------------------------------------------------------------------------

class TestDistributeXp:

    def test_xp_split_evenly(self):
        state = _make_active_state(n_chars=2)
        distribute_xp(state, 200)
        for char in state.characters.values():
            assert char.experience == 100

    def test_xp_floor_division(self):
        """100 XP / 3 chars = 33 each; 1 XP is discarded."""
        state = _make_active_state(n_chars=3)
        distribute_xp(state, 100)
        for char in state.characters.values():
            assert char.experience == 33

    def test_zero_xp_does_nothing(self):
        state = _make_active_state()
        result = distribute_xp(state, 0)
        assert result == []
        char = list(state.characters.values())[0]
        assert char.experience == 0

    def test_inactive_chars_excluded(self):
        state = _make_active_state(n_chars=2)
        chars = list(state.characters.values())
        chars[0].status = CharacterStatus.DEAD
        distribute_xp(state, 100)
        assert chars[0].experience == 0
        assert chars[1].experience == 100

    def test_no_active_chars_returns_empty(self):
        state = _make_active_state()
        char = list(state.characters.values())[0]
        char.status = CharacterStatus.DEAD
        result = distribute_xp(state, 100)
        assert result == []

    def test_level_up_propagated(self):
        """Award enough XP to cross level threshold → LevelUpResult returned."""
        state = _make_active_state()
        results = distribute_xp(state, 2000)
        assert len(results) == 1
        assert results[0].new_level == 2

    def test_returns_flat_level_up_list(self):
        """2 chars both leveling up → 2 LevelUpResult objects."""
        state = _make_active_state(n_chars=2)
        # 2000 XP / 2 chars = 1000 each — not enough to level (need 2000)
        # Award 4000 total → 2000 each → both level up
        results = distribute_xp(state, 4000)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# NPC kill XP
# ---------------------------------------------------------------------------

class TestNpcKillXp:

    def test_kill_awards_xp(self):
        """Killing a 1-HD NPC awards 100 XP to the sole character."""
        state, char_ids, npc_id = _make_combat_state(hit_dice=1)
        state.current_turn.submissions = [_attack_submission(char_ids[0], npc_id)]
        auto_resolve_round(state)
        char = state.characters[char_ids[0]]
        assert char.experience == 100

    def test_kill_xp_scales_with_hit_dice(self):
        """A 3-HD NPC awards 300 XP."""
        state, char_ids, npc_id = _make_combat_state(hit_dice=3)
        state.current_turn.submissions = [_attack_submission(char_ids[0], npc_id)]
        auto_resolve_round(state)
        char = state.characters[char_ids[0]]
        assert char.experience == 300

    def test_kill_xp_split_among_party(self):
        """2-char party kills a 1-HD NPC → 50 XP each.

        Only one character submits an attack; the other idles. The kill XP is
        still distributed among both ACTIVE characters via distribute_xp.
        """
        state, char_ids, npc_id = _make_combat_state(hit_dice=1, n_chars=2)
        # Only char[0] attacks; char[1] idles — avoids double-awarding XP
        # if char[1] were to attack the already-dead NPC's corpse.
        state.current_turn.submissions = [_attack_submission(char_ids[0], npc_id)]
        auto_resolve_round(state)
        for cid in char_ids:
            assert state.characters[cid].experience == 50

    def test_kill_log_contains_xp_message(self):
        """Combat narrative mentions XP after an NPC kill."""
        state, char_ids, npc_id = _make_combat_state(hit_dice=1)
        state.current_turn.submissions = [_attack_submission(char_ids[0], npc_id)]
        result = auto_resolve_round(state)
        assert "XP" in result.message

    def test_dead_char_excluded_from_split(self):
        """A DEAD character does not receive XP from an NPC kill."""
        state, char_ids, npc_id = _make_combat_state(hit_dice=1, n_chars=2)
        # Mark the first character as dead before the round
        state.characters[char_ids[0]].status = CharacterStatus.DEAD
        state.battlefield.combatants.pop(char_ids[0], None)
        state.current_turn.submissions = [_attack_submission(char_ids[1], npc_id)]
        auto_resolve_round(state)
        assert state.characters[char_ids[0]].experience == 0
        assert state.characters[char_ids[1]].experience == 100


# ---------------------------------------------------------------------------
# Room exploration XP
# ---------------------------------------------------------------------------

class TestRoomExplorationXp:

    def _state_with_room(self, n_chars=1, exploration_xp=0):
        """Return (state, room) with the room registered but not yet visited."""
        state = _make_active_state(n_chars=n_chars)
        room = Room(name="Crypt", description="A cold room.", exploration_xp=exploration_xp)
        register_room(state, room)
        return state, room

    def test_first_visit_awards_xp(self):
        state, room = self._state_with_room()
        move_party_to_room(state, room.room_id)
        char = list(state.characters.values())[0]
        assert char.experience == DEFAULT_ROOM_XP

    def test_second_visit_no_xp(self):
        state, room = self._state_with_room()
        move_party_to_room(state, room.room_id)   # first visit → XP
        char = list(state.characters.values())[0]
        xp_after_first = char.experience
        move_party_to_room(state, room.room_id)   # second visit → no XP
        assert char.experience == xp_after_first

    def test_set_room_always_awards_xp(self):
        state = _make_active_state()
        room = Room(name="Vault", description="A vault.")
        set_room(state, room)
        char = list(state.characters.values())[0]
        assert char.experience == DEFAULT_ROOM_XP

    def test_custom_exploration_xp(self):
        state, room = self._state_with_room(exploration_xp=250)
        move_party_to_room(state, room.room_id)
        char = list(state.characters.values())[0]
        assert char.experience == 250

    def test_default_xp_from_constant(self):
        """exploration_xp=0 falls back to DEFAULT_ROOM_XP."""
        state, room = self._state_with_room(exploration_xp=0)
        move_party_to_room(state, room.room_id)
        char = list(state.characters.values())[0]
        assert char.experience == DEFAULT_ROOM_XP

    def test_exploration_xp_split_in_party(self):
        """2-char party entering an unvisited room → 50 XP each (100 // 2)."""
        state, room = self._state_with_room(n_chars=2)
        move_party_to_room(state, room.room_id)
        for char in state.characters.values():
            assert char.experience == DEFAULT_ROOM_XP // 2

    def test_exploration_xp_message(self):
        state, room = self._state_with_room()
        result = move_party_to_room(state, room.room_id)
        assert "XP" in result.message


# ---------------------------------------------------------------------------
# Serialization: hit_dice and exploration_xp
# ---------------------------------------------------------------------------

class TestNpcHitDiceSerialization:

    def test_hit_dice_round_trips(self):
        npc = NPC(name="Troll", hp_max=20, hp_current=20, defense=2,
                  damage_dice="2d6", hit_dice=4)
        data = serialize_npc(npc)
        loaded = deserialize_npc(data)
        assert loaded.hit_dice == 4

    def test_missing_hit_dice_defaults_to_1(self):
        """Old NPC JSON without a hit_dice key migrates to 1."""
        npc = NPC(name="Rat", hp_max=2, hp_current=2, defense=0,
                  damage_dice="1d4", hit_dice=3)
        data = serialize_npc(npc)
        del data["hit_dice"]          # simulate old format
        loaded = deserialize_npc(data)
        assert loaded.hit_dice == 1

    def test_room_exploration_xp_round_trips(self):
        room = Room(name="Library", description="Dusty shelves.", exploration_xp=500)
        data = serialize_room(room)
        loaded = deserialize_room(data)
        assert loaded.exploration_xp == 500

    def test_room_missing_exploration_xp_defaults_to_0(self):
        """Old Room JSON without exploration_xp key migrates to 0."""
        room = Room(name="Hall", description="A hall.", exploration_xp=200)
        data = serialize_room(room)
        del data["exploration_xp"]    # simulate old format
        loaded = deserialize_room(data)
        assert loaded.exploration_xp == 0
