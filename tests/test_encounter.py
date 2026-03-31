"""
test_encounter.py — Random encounter system tests.

Tests cover check_random_encounter() directly, so all cases are deterministic.
random.randint is patched where the roll outcome matters.
"""

from unittest.mock import patch

from engine.encounter import check_random_encounter
from models import (
    NPC,
    Dungeon,
    EncounterEntry,
    NPCGroup,
    Room,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _npc_group(name="Skeletons"):
    npc = NPC(name=f"{name} 1", hp_max=6, hp_current=6)
    return NPCGroup(name=name, npcs=[npc])


def _dungeon_with_roster(interval=6, roll="1d6", weights=None):
    """Return a Dungeon with one room and a populated encounter roster."""
    room = Room(name="Crypt")
    d = Dungeon(name="Test Dungeon", random_encounter_interval=interval, random_encounter_roll=roll)
    d.rooms[room.room_id] = room
    d.entrance_id = room.room_id
    entries = []
    for i, w in enumerate(weights or [1]):
        entries.append(EncounterEntry(npc_group=_npc_group(f"Group {i}"), weight=w))
    d.random_encounter_roster = entries
    return d, room.room_id


# ---------------------------------------------------------------------------
# Guard conditions — should return None without firing
# ---------------------------------------------------------------------------

class TestEncounterGuards:
    def test_no_dungeon_returns_none(self, bare_state):
        assert bare_state.dungeon is None
        result = check_random_encounter(bare_state)
        assert result is None

    def test_empty_roster_returns_none(self, bare_state):
        bare_state.dungeon = Dungeon(name="Empty")
        # roster is empty by default
        result = check_random_encounter(bare_state)
        assert result is None

    def test_interval_not_reached_returns_none(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=6)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 4          # 4 - 0 = 4, interval is 6
        result = check_random_encounter(bare_state)
        assert result is None

    def test_interval_not_reached_does_not_update_checkpoint(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=6)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        check_random_encounter(bare_state)
        assert bare_state.last_encounter_check_turn == 0  # unchanged

    def test_safe_room_modifier_zero_returns_none(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        dungeon.rooms[room_id].random_encounter_modifier = 0.0
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        result = check_random_encounter(bare_state)
        assert result is None

    def test_safe_room_still_advances_checkpoint(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        dungeon.rooms[room_id].random_encounter_modifier = 0.0
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        check_random_encounter(bare_state)
        assert bare_state.last_encounter_check_turn == 3

    def test_modifier_below_one_gives_threshold_zero(self, bare_state):
        """modifier=0.5 → floor(0.5) = 0 → no encounter possible."""
        dungeon, room_id = _dungeon_with_roster(interval=3)
        dungeon.rooms[room_id].random_encounter_modifier = 0.5
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        result = check_random_encounter(bare_state)
        assert result is None

    def test_roll_above_threshold_returns_none(self, bare_state):
        """Roll of 2 with modifier 1.0 (threshold 1) → no encounter."""
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 2}):
            result = check_random_encounter(bare_state)
        assert result is None


# ---------------------------------------------------------------------------
# Encounter fires
# ---------------------------------------------------------------------------

class TestEncounterFires:
    def test_encounter_fires_on_roll_of_one(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            result = check_random_encounter(bare_state)
        assert result is not None
        assert result.ok

    def test_encounter_adds_group_to_roster(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        assert len(bare_state.npc_roster.groups) == 0
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        assert len(bare_state.npc_roster.groups) == 1

    def test_spawned_group_placed_in_current_room(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        spawned = list(bare_state.npc_roster.groups.values())[0]
        assert spawned.current_room_id == room_id

    def test_encounter_message_references_group_name(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            result = check_random_encounter(bare_state)
        assert "Group 0" in result.message

    def test_checkpoint_advances_after_encounter(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        assert bare_state.last_encounter_check_turn == 3

    def test_doubled_modifier_raises_threshold(self, bare_state):
        """modifier=2.0 → threshold=2 → roll of 2 should trigger encounter."""
        dungeon, room_id = _dungeon_with_roster(interval=3)
        dungeon.rooms[room_id].random_encounter_modifier = 2.0
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 2}):
            result = check_random_encounter(bare_state)
        assert result is not None
        assert result.ok

    def test_second_check_before_next_interval_returns_none(self, bare_state):
        """After a check fires at turn 6, turn 8 (< 6+6=12) should not check."""
        dungeon, room_id = _dungeon_with_roster(interval=6)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 6
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        bare_state.turn_number = 8
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            result = check_random_encounter(bare_state)
        assert result is None


# ---------------------------------------------------------------------------
# Deep copy independence
# ---------------------------------------------------------------------------

class TestDeepCopy:
    def test_spawned_group_has_different_group_id_than_template(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        template_id = dungeon.random_encounter_roster[0].npc_group.group_id
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        spawned = list(bare_state.npc_roster.groups.values())[0]
        assert spawned.group_id != template_id

    def test_two_encounters_produce_independent_groups(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        # advance past next interval
        bare_state.turn_number = 6
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        groups = list(bare_state.npc_roster.groups.values())
        assert len(groups) == 2
        assert groups[0].group_id != groups[1].group_id

    def test_modifying_spawned_npc_does_not_affect_template(self, bare_state):
        dungeon, room_id = _dungeon_with_roster(interval=3)
        bare_state.dungeon = dungeon
        bare_state.current_room_id = room_id
        bare_state.turn_number = 3
        template_hp = dungeon.random_encounter_roster[0].npc_group.npcs[0].hp_max
        with patch("engine.encounter.roll_dice_expr", return_value={"total": 1}):
            check_random_encounter(bare_state)
        spawned = list(bare_state.npc_roster.groups.values())[0]
        spawned.npcs[0].hp_current = 0
        assert dungeon.random_encounter_roster[0].npc_group.npcs[0].hp_max == template_hp
