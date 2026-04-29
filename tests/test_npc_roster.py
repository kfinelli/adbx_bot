"""
tests/test_npc_roster.py — Tests for NPC roster management and encounter roster engine methods.

Covers:
  - add_npc_to_group: adds NPC to correct group, error on bad group_id
  - update_group: name, movement_logic, current_room_id, possible_rooms all update
  - add_encounter_entry: appends to roster, error when no dungeon
  - remove_encounter_entry: removes by group_id, error on not found
  - update_encounter_entry_weight: updates weight correctly
  - promote_group_to_encounter: copy is independent; live group still exists; current_room_id cleared
  - update_encounter_npc / remove_encounter_npc / add_npc_to_encounter_group
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    add_encounter_entry,
    add_npc_to_encounter_group,
    add_npc_to_group,
    promote_group_to_encounter,
    remove_encounter_entry,
    remove_encounter_npc,
    update_encounter_entry_weight,
    update_encounter_npc,
    update_group,
)
from models import (
    NPC,
    Dungeon,
    GameState,
    NPCGroup,
    NPCMovementLogic,
    Party,
    Room,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state_with_dungeon() -> tuple[GameState, Room, Room]:
    """State with a minimal dungeon (two rooms) and an NPC group in room1."""
    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")

    room1 = Room(name="Hall")
    room2 = Room(name="Vault")
    dungeon = Dungeon(name="Test Dungeon")
    dungeon.rooms[room1.room_id] = room1
    dungeon.rooms[room2.room_id] = room2
    state.dungeon = dungeon

    npc = NPC(name="Goblin A", hp_max=10, hp_current=10, defense=1)
    group = NPCGroup(name="Goblins", npcs=[npc], current_room_id=room1.room_id)
    state.npc_roster.add_group(group)

    return state, room1, room2


def _get_only_group(state: GameState) -> NPCGroup:
    return next(iter(state.npc_roster.groups.values()))


# ---------------------------------------------------------------------------
# add_npc_to_group
# ---------------------------------------------------------------------------

def test_add_npc_to_group_success():
    state, room1, _ = _make_state_with_dungeon()
    group = _get_only_group(state)
    new_npc = NPC(name="Goblin B", hp_max=8, hp_current=8)

    result = add_npc_to_group(state, group.group_id, new_npc)

    assert result.ok
    assert len(group.npcs) == 2
    assert group.npcs[1].name == "Goblin B"


def test_add_npc_to_group_bad_id():
    state, _, _ = _make_state_with_dungeon()
    fake_id = uuid4()
    npc = NPC(name="Ghost", hp_max=5, hp_current=5)

    result = add_npc_to_group(state, fake_id, npc)

    assert not result.ok


# ---------------------------------------------------------------------------
# update_group
# ---------------------------------------------------------------------------

def test_update_group_name_and_logic():
    state, room1, room2 = _make_state_with_dungeon()
    group = _get_only_group(state)

    result = update_group(
        state, group.group_id,
        name="Wandering Goblins",
        movement_logic=NPCMovementLogic.WANDERING,
        current_room_id=room2.room_id,
        possible_rooms=[room1.room_id, room2.room_id],
    )

    assert result.ok
    assert group.name == "Wandering Goblins"
    assert group.movement_logic == NPCMovementLogic.WANDERING
    assert group.current_room_id == room2.room_id
    assert room1.room_id in group.possible_rooms
    assert room2.room_id in group.possible_rooms


def test_update_group_clear_name():
    state, room1, _ = _make_state_with_dungeon()
    group = _get_only_group(state)

    result = update_group(
        state, group.group_id,
        name="",
        movement_logic=NPCMovementLogic.STATIONARY,
        current_room_id=room1.room_id,
        possible_rooms=[],
    )

    assert result.ok
    assert group.name is None


def test_update_group_bad_id():
    state, _, _ = _make_state_with_dungeon()
    result = update_group(state, uuid4(), "", NPCMovementLogic.STATIONARY, None, [])
    assert not result.ok


# ---------------------------------------------------------------------------
# add_encounter_entry
# ---------------------------------------------------------------------------

def test_add_encounter_entry():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(name="Skeletons", npcs=[NPC(name="Sk A", hp_max=6, hp_current=6)])

    result = add_encounter_entry(state, template, weight=3)

    assert result.ok
    assert len(state.dungeon.random_encounter_roster) == 1
    assert state.dungeon.random_encounter_roster[0].weight == 3
    assert state.dungeon.random_encounter_roster[0].npc_group.name == "Skeletons"


def test_add_encounter_entry_no_dungeon():
    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    template = NPCGroup(name="X", npcs=[NPC(name="X", hp_max=5, hp_current=5)])

    result = add_encounter_entry(state, template, weight=1)

    assert not result.ok


def test_add_encounter_entry_weight_minimum():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(npcs=[NPC(name="Rat", hp_max=2, hp_current=2)])

    add_encounter_entry(state, template, weight=0)

    assert state.dungeon.random_encounter_roster[0].weight == 1


# ---------------------------------------------------------------------------
# remove_encounter_entry
# ---------------------------------------------------------------------------

def test_remove_encounter_entry():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(npcs=[NPC(name="Orc", hp_max=12, hp_current=12)])
    add_encounter_entry(state, template, weight=2)
    entry_group_id = state.dungeon.random_encounter_roster[0].npc_group.group_id

    result = remove_encounter_entry(state, entry_group_id)

    assert result.ok
    assert len(state.dungeon.random_encounter_roster) == 0


def test_remove_encounter_entry_not_found():
    state, _, _ = _make_state_with_dungeon()
    result = remove_encounter_entry(state, uuid4())
    assert not result.ok


# ---------------------------------------------------------------------------
# update_encounter_entry_weight
# ---------------------------------------------------------------------------

def test_update_encounter_entry_weight():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(npcs=[NPC(name="Troll", hp_max=30, hp_current=30)])
    add_encounter_entry(state, template, weight=1)
    group_id = state.dungeon.random_encounter_roster[0].npc_group.group_id

    result = update_encounter_entry_weight(state, group_id, 5)

    assert result.ok
    assert state.dungeon.random_encounter_roster[0].weight == 5


def test_update_encounter_entry_weight_not_found():
    state, _, _ = _make_state_with_dungeon()
    result = update_encounter_entry_weight(state, uuid4(), 3)
    assert not result.ok


# ---------------------------------------------------------------------------
# promote_group_to_encounter
# ---------------------------------------------------------------------------

def test_promote_group_to_encounter_copy_is_independent():
    state, room1, _ = _make_state_with_dungeon()
    live_group = _get_only_group(state)
    original_group_id = live_group.group_id
    original_npc_id = live_group.npcs[0].npc_id

    result = promote_group_to_encounter(state, live_group.group_id, weight=2)

    assert result.ok
    # Live group still exists
    assert original_group_id in state.npc_roster.groups

    entry = state.dungeon.random_encounter_roster[0]
    # Template is a different object with different IDs
    assert entry.npc_group.group_id != original_group_id
    assert entry.npc_group.npcs[0].npc_id != original_npc_id
    assert entry.weight == 2


def test_promote_group_to_encounter_clears_room():
    state, room1, _ = _make_state_with_dungeon()
    live_group = _get_only_group(state)
    assert live_group.current_room_id == room1.room_id

    promote_group_to_encounter(state, live_group.group_id, weight=1)

    entry = state.dungeon.random_encounter_roster[0]
    # Template has no room assignment
    assert entry.npc_group.current_room_id is None


def test_promote_group_to_encounter_bad_id():
    state, _, _ = _make_state_with_dungeon()
    result = promote_group_to_encounter(state, uuid4(), weight=1)
    assert not result.ok


# ---------------------------------------------------------------------------
# update_encounter_npc / remove_encounter_npc / add_npc_to_encounter_group
# ---------------------------------------------------------------------------

def test_update_encounter_npc():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(name="Orcs", npcs=[NPC(name="Orc A", hp_max=15, hp_current=15)])
    add_encounter_entry(state, template, weight=1)
    entry = state.dungeon.random_encounter_roster[0]
    eg_id = entry.npc_group.group_id
    npc_id = entry.npc_group.npcs[0].npc_id

    result = update_encounter_npc(state, eg_id, npc_id, "Orc Chief", "", 25, 3)

    assert result.ok
    npc = entry.npc_group.npcs[0]
    assert npc.name == "Orc Chief"
    assert npc.hp_max == 25
    assert npc.defense == 3


def test_remove_encounter_npc():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(npcs=[NPC(name="Bat", hp_max=3, hp_current=3)])
    add_encounter_entry(state, template, weight=1)
    entry = state.dungeon.random_encounter_roster[0]
    eg_id = entry.npc_group.group_id
    npc_id = entry.npc_group.npcs[0].npc_id

    result = remove_encounter_npc(state, eg_id, npc_id)

    assert result.ok
    assert len(entry.npc_group.npcs) == 0


def test_add_npc_to_encounter_group():
    state, _, _ = _make_state_with_dungeon()
    template = NPCGroup(name="Pack", npcs=[NPC(name="Wolf A", hp_max=8, hp_current=8)])
    add_encounter_entry(state, template, weight=1)
    entry = state.dungeon.random_encounter_roster[0]
    eg_id = entry.npc_group.group_id
    new_npc = NPC(name="Wolf B", hp_max=8, hp_current=8)

    result = add_npc_to_encounter_group(state, eg_id, new_npc)

    assert result.ok
    assert len(entry.npc_group.npcs) == 2
    assert entry.npc_group.npcs[1].name == "Wolf B"
