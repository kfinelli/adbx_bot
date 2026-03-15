"""
test_dungeon.py — Dungeon graph, rooms, features, exits, NPCs, light sources.
"""

from uuid import uuid4

from engine import (
    abscond,
    add_exit,
    add_npc,
    delete_exit,
    delete_feature,
    import_dungeon,
    move_party_to_room,
    register_room,
    remove_npc,
    set_exit_state,
    set_feature_state,
    set_light_source,
    set_npc_hp,
    set_npc_status,
    set_room,
    update_exit,
    update_feature,
    update_npc,
    update_room,
)
from models import NPC, DoorState, Dungeon, Room, RoomFeature

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_room(name="Test Room", description="A room."):
    return Room(name=name, description=description)

def _make_npc(name="Goblin", hp=4):
    return NPC(name=name, hp_max=hp, hp_current=hp)


# ---------------------------------------------------------------------------
# Room management
# ---------------------------------------------------------------------------

class TestRegisterRoom:
    def test_register_adds_room(self, bare_state):
        room = _make_room("Entry Hall")
        result = register_room(bare_state, room)
        assert result.ok
        assert room.room_id in bare_state.dungeon.rooms

    def test_register_does_not_move_party(self, bare_state):
        room = _make_room("Vault")
        register_room(bare_state, room)
        assert bare_state.current_room_id != room.room_id

    def test_register_creates_dungeon_if_absent(self, bare_state):
        assert bare_state.dungeon is None
        register_room(bare_state, _make_room())
        assert bare_state.dungeon is not None


class TestSetRoom:
    def test_set_room_moves_party(self, bare_state):
        room = _make_room("Great Hall")
        result = set_room(bare_state, room)
        assert result.ok
        assert bare_state.current_room_id == room.room_id

    def test_set_room_marks_visited(self, bare_state):
        room = _make_room()
        set_room(bare_state, room)
        assert bare_state.dungeon.rooms[room.room_id].visited is True

    def test_set_room_clears_npcs(self, bare_state):
        # Add an NPC to the current room via the roster
        from models import NPCGroup
        npc = _make_npc()
        group = NPCGroup(npcs=[npc], current_room_id=bare_state.current_room_id)
        bare_state.npc_roster.add_group(group)
        
        # Move to a new room
        set_room(bare_state, _make_room())
        
        # The old room's NPCs should not be in the new room
        assert bare_state.npcs_in_current_room == []


class TestMovePartyToRoom:
    def test_move_succeeds(self, bare_state):
        room = _make_room("Corridor")
        register_room(bare_state, room)
        result = move_party_to_room(bare_state, room.room_id)
        assert result.ok
        assert bare_state.current_room_id == room.room_id

    def test_move_to_unknown_room_fails(self, bare_state):
        register_room(bare_state, _make_room())
        result = move_party_to_room(bare_state, uuid4())
        assert not result.ok

    def test_move_without_dungeon_fails(self, bare_state):
        result = move_party_to_room(bare_state, uuid4())
        assert not result.ok


class TestUpdateRoom:
    def test_update_room_name_and_description(self, bare_state):
        room = _make_room("Old Name")
        register_room(bare_state, room)
        result = update_room(bare_state, room.room_id, "New Name", "New desc.")
        assert result.ok
        assert bare_state.dungeon.rooms[room.room_id].name == "New Name"

    def test_update_room_empty_name_fails(self, bare_state):
        room = _make_room()
        register_room(bare_state, room)
        result = update_room(bare_state, room.room_id, "  ", "desc")
        assert not result.ok

    def test_update_unknown_room_fails(self, bare_state):
        register_room(bare_state, _make_room())
        result = update_room(bare_state, uuid4(), "Name", "desc")
        assert not result.ok


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class TestFeatures:
    def _room_with_feature(self, state):
        room = _make_room()
        feat = RoomFeature(name="Iron Door", description="Heavy.", state="closed")
        room.features.append(feat)
        set_room(state, room)
        return room, feat

    def test_set_feature_state(self, bare_state):
        room, feat = self._room_with_feature(bare_state)
        result = set_feature_state(bare_state, feat.feature_id, "open")
        assert result.ok
        assert room.features[0].state == "open"

    def test_set_feature_state_unknown_fails(self, bare_state):
        self._room_with_feature(bare_state)
        result = set_feature_state(bare_state, uuid4(), "open")
        assert not result.ok

    def test_update_feature(self, bare_state):
        room, feat = self._room_with_feature(bare_state)
        result = update_feature(
            bare_state, feat.feature_id,
            name="Iron Gate", description="Rusty.", state_str="locked"
        )
        assert result.ok
        assert room.features[0].name == "Iron Gate"
        assert room.features[0].state == "locked"

    def test_update_feature_empty_name_fails(self, bare_state):
        room, feat = self._room_with_feature(bare_state)
        result = update_feature(bare_state, feat.feature_id, "", "desc", "state")
        assert not result.ok

    def test_delete_feature(self, bare_state):
        room, feat = self._room_with_feature(bare_state)
        result = delete_feature(bare_state, feat.feature_id)
        assert result.ok
        assert len(room.features) == 0

    def test_delete_unknown_feature_fails(self, bare_state):
        self._room_with_feature(bare_state)
        result = delete_feature(bare_state, uuid4())
        assert not result.ok


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

class TestExits:
    def test_add_exit(self, bare_state):
        set_room(bare_state, _make_room())
        result = add_exit(bare_state, "north", "A stone archway.")
        assert result.ok
        assert len(bare_state.current_room.exits) == 1

    def test_set_exit_state(self, bare_state):
        set_room(bare_state, _make_room())
        add_exit(bare_state, "north", "Archway.")
        exit_ = bare_state.current_room.exits[0]
        result = set_exit_state(bare_state, exit_.exit_id, DoorState.LOCKED)
        assert result.ok
        assert bare_state.current_room.exits[0].door_state == DoorState.LOCKED

    def test_update_exit(self, bare_state):
        set_room(bare_state, _make_room())
        add_exit(bare_state, "north", "Archway.")
        exit_ = bare_state.current_room.exits[0]
        result = update_exit(
            bare_state, exit_.exit_id,
            label="south", description="A trapdoor.",
            door_state=DoorState.OPEN,
        )
        assert result.ok
        assert bare_state.current_room.exits[0].label == "south"

    def test_update_exit_empty_label_fails(self, bare_state):
        set_room(bare_state, _make_room())
        add_exit(bare_state, "north", "Archway.")
        exit_ = bare_state.current_room.exits[0]
        result = update_exit(
            bare_state, exit_.exit_id, label="", description="",
            door_state=DoorState.OPEN,
        )
        assert not result.ok

    def test_delete_exit(self, bare_state):
        set_room(bare_state, _make_room())
        add_exit(bare_state, "north", "Archway.")
        exit_ = bare_state.current_room.exits[0]
        result = delete_exit(bare_state, exit_.exit_id)
        assert result.ok
        assert len(bare_state.current_room.exits) == 0

    def test_delete_unknown_exit_fails(self, bare_state):
        set_room(bare_state, _make_room())
        result = delete_exit(bare_state, uuid4())
        assert not result.ok


# ---------------------------------------------------------------------------
# Abscond
# ---------------------------------------------------------------------------

class TestAbscond:
    def test_abscond_through_open_exit(self, active_state):
        char_id = list(active_state.party.member_ids)[0]
        active_state.party.leader_id = char_id
        set_room(active_state, _make_room("Entry"))
        add_exit(active_state, "north", "Dark passage.")
        result = abscond(active_state, char_id, 1)
        assert result.ok
        assert result.notify_dm is True

    def test_abscond_non_leader_fails(self, active_state):
        char_id = list(active_state.party.member_ids)[0]
        # Ensure no leader is set (or set to someone else)
        active_state.party.leader_id = None
        set_room(active_state, _make_room())
        add_exit(active_state, "north", "Passage.")
        result = abscond(active_state, char_id, 1)
        assert not result.ok

    def test_abscond_locked_exit_fails(self, active_state):
        char_id = list(active_state.party.member_ids)[0]
        active_state.party.leader_id = char_id
        set_room(active_state, _make_room())
        add_exit(active_state, "north", "Locked door.", door_state=DoorState.LOCKED)
        result = abscond(active_state, char_id, 1)
        assert not result.ok

    def test_abscond_out_of_range_fails(self, active_state):
        char_id = list(active_state.party.member_ids)[0]
        active_state.party.leader_id = char_id
        set_room(active_state, _make_room())
        add_exit(active_state, "north", "Passage.")
        result = abscond(active_state, char_id, 99)
        assert not result.ok


# ---------------------------------------------------------------------------
# NPCs
# ---------------------------------------------------------------------------

class TestNPCs:
    def test_add_npc(self, bare_state):
        # Set up a room first since NPCs need a current room
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc("Goblin Scout")
        result = add_npc(bare_state, npc)
        assert result.ok
        assert len(bare_state.npcs_in_current_room) == 1

    def test_set_npc_hp(self, bare_state):
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc(hp=10)
        add_npc(bare_state, npc)
        result = set_npc_hp(bare_state, npc.npc_id, 3)
        assert result.ok
        assert bare_state.npcs_in_current_room[0].hp_current == 3

    def test_npc_hp_zero_sets_dead(self, bare_state):
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc(hp=4)
        add_npc(bare_state, npc)
        set_npc_hp(bare_state, npc.npc_id, 0)
        assert bare_state.npcs_in_current_room[0].status == "dead"

    def test_set_npc_status(self, bare_state):
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc()
        add_npc(bare_state, npc)
        result = set_npc_status(bare_state, npc.npc_id, "fled")
        assert result.ok
        assert bare_state.npcs_in_current_room[0].status == "fled"

    def test_update_npc(self, bare_state):
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc("Goblin")
        add_npc(bare_state, npc)
        result = update_npc(
            bare_state, npc.npc_id,
            name="Hobgoblin", description="Bigger.", hp_max=8, hp_current=8,
            armor_class=6
        )
        assert result.ok
        assert bare_state.npcs_in_current_room[0].name == "Hobgoblin"
        assert bare_state.npcs_in_current_room[0].hp_max == 8

    def test_update_npc_empty_name_fails(self, bare_state):
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc()
        add_npc(bare_state, npc)
        result = update_npc(bare_state, npc.npc_id, name="", description="",
                            hp_max=4, hp_current=4, armor_class=9)
        assert not result.ok

    def test_remove_npc(self, bare_state):
        from engine import set_room
        from models import Room
        room = _make_room("Test Room")
        set_room(bare_state, room)
        
        npc = _make_npc()
        add_npc(bare_state, npc)
        result = remove_npc(bare_state, npc.npc_id)
        assert result.ok
        assert len(bare_state.npcs_in_current_room) == 0

    def test_remove_unknown_npc_fails(self, bare_state):
        result = remove_npc(bare_state, uuid4())
        assert not result.ok


# ---------------------------------------------------------------------------
# Light sources
# ---------------------------------------------------------------------------

class TestLightSources:
    def test_set_light_source(self, bare_state):
        result = set_light_source(bare_state, "Torch", 6)
        assert result.ok
        assert bare_state.party.active_light.label == "Torch"
        assert bare_state.party.active_light.turns_remaining == 6

    def test_permanent_light_source(self, bare_state):
        result = set_light_source(bare_state, "Continual Light", None)
        assert result.ok
        assert bare_state.party.active_light.turns_remaining is None

    def test_new_light_deactivates_old(self, bare_state):
        set_light_source(bare_state, "Torch", 6)
        set_light_source(bare_state, "Lantern", 24)
        active = bare_state.party.active_light
        assert active.label == "Lantern"
        # Torch should be inactive
        for ls in bare_state.party.light_sources:
            if ls.label == "Torch":
                assert ls.is_active is False

    def test_light_ticks_on_resolve(self, active_state):
        set_light_source(active_state, "Torch", 6)
        from engine import close_turn, resolve_turn
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")
        assert active_state.party.active_light.turns_remaining == 5

    def test_light_does_not_go_below_zero(self, active_state):
        set_light_source(active_state, "Torch", 0)
        from engine import close_turn, resolve_turn
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")
        assert active_state.party.active_light.turns_remaining == 0

    def test_set_light_no_party_fails(self):
        from models import GameState
        state = GameState(platform_channel_id="ch", dm_user_id="dm")
        result = set_light_source(state, "Torch", 6)
        assert not result.ok


# ---------------------------------------------------------------------------
# Dungeon import
# ---------------------------------------------------------------------------

class TestImportDungeon:
    def _make_dungeon(self):
        room = _make_room("Entrance Hall")
        d = Dungeon(name="Test Keep")
        d.rooms[room.room_id] = room
        d.entrance_id = room.room_id
        return d

    def test_import_succeeds_in_pre_start(self, bare_state):
        dungeon = self._make_dungeon()
        result = import_dungeon(bare_state, dungeon)
        assert result.ok
        assert bare_state.dungeon is dungeon

    def test_import_sets_current_room_to_entrance(self, bare_state):
        dungeon = self._make_dungeon()
        import_dungeon(bare_state, dungeon)
        assert bare_state.current_room_id == dungeon.entrance_id

    def test_import_blocked_after_start(self, active_state):
        dungeon = self._make_dungeon()
        result = import_dungeon(active_state, dungeon)
        assert not result.ok
        assert "PRE_START" in result.error or "before" in result.error.lower()
