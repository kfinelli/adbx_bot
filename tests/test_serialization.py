"""
test_serialization.py — Serialization round-trip and dungeon file format.

These tests ensure that any GameState can be saved and restored identically,
which is the most critical property for persistence correctness.
"""

import json
import pytest
from uuid import UUID
from models import (
    CharacterStatus, DoorState, Dungeon, GameState, NPC,
    Party, Room, RoomFeature, Exit, SessionMode,
)
from engine import (
    add_exit, add_npc, close_turn, create_character,
    register_room, resolve_turn, set_light_source, set_room,
    start_session, CharacterClass,
)
from serialization import (
    deserialize_dungeon_file,
    deserialize_state,
    serialize_dungeon_file,
    serialize_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roundtrip(state: GameState) -> GameState:
    """Serialize then deserialize — the restored state must equal the original."""
    return deserialize_state(serialize_state(state))


# ---------------------------------------------------------------------------
# GameState round-trip
# ---------------------------------------------------------------------------

class TestStateRoundTrip:
    def test_bare_state_roundtrips(self, bare_state):
        restored = _roundtrip(bare_state)
        assert restored.platform_channel_id == bare_state.platform_channel_id
        assert restored.dm_user_id == bare_state.dm_user_id
        assert restored.mode == SessionMode.PRE_START

    def test_session_id_preserved(self, bare_state):
        restored = _roundtrip(bare_state)
        assert restored.session_id == bare_state.session_id

    def test_character_roundtrips(self, state_with_fighter):
        restored = _roundtrip(state_with_fighter)
        assert len(restored.characters) == 1
        orig = list(state_with_fighter.characters.values())[0]
        rest = list(restored.characters.values())[0]
        assert rest.name == orig.name
        assert rest.character_class == orig.character_class
        assert rest.hp_max == orig.hp_max
        assert rest.hp_current == orig.hp_current
        assert rest.armor_class == orig.armor_class

    def test_party_membership_preserved(self, state_with_fighter):
        restored = _roundtrip(state_with_fighter)
        assert len(restored.party.member_ids) == len(state_with_fighter.party.member_ids)

    def test_ability_scores_preserved(self, state_with_fighter):
        orig_char = list(state_with_fighter.characters.values())[0]
        restored = _roundtrip(state_with_fighter)
        rest_char = list(restored.characters.values())[0]
        orig_scores = orig_char.ability_scores
        rest_scores = rest_char.ability_scores
        assert rest_scores.strength == orig_scores.strength
        assert rest_scores.constitution == orig_scores.constitution

    def test_saving_throws_preserved(self, state_with_fighter):
        orig = list(state_with_fighter.characters.values())[0]
        restored = _roundtrip(state_with_fighter)
        rest = list(restored.characters.values())[0]
        assert rest.saving_throws == orig.saving_throws

    def test_spellbook_roundtrips(self, bare_state):
        create_character(bare_state, "Mira", CharacterClass.MAGIC_USER, "Pack A")
        restored = _roundtrip(bare_state)
        char = list(restored.characters.values())[0]
        assert char.spellbook is not None
        assert len(char.spellbook.max_slots) == 6

    def test_active_state_roundtrips(self, active_state):
        restored = _roundtrip(active_state)
        assert restored.mode == SessionMode.EXPLORATION
        assert restored.session_active is True
        assert restored.current_turn is not None

    def test_turn_history_preserved(self, active_state):
        close_turn(active_state)
        resolve_turn(active_state, "First resolution.")
        assert len(active_state.turn_history) == 1
        restored = _roundtrip(active_state)
        assert len(restored.turn_history) == 1
        assert restored.turn_history[0].resolution == "First resolution."

    def test_npc_roundtrips(self, bare_state):
        npc = NPC(name="Goblin", hp_max=4, hp_current=2, armor_class=7)
        bare_state.npcs.append(npc)
        restored = _roundtrip(bare_state)
        assert len(restored.npcs) == 1
        r = restored.npcs[0]
        assert r.name == "Goblin"
        assert r.hp_current == 2
        assert r.armor_class == 7

    def test_light_source_roundtrips(self, bare_state):
        set_light_source(bare_state, "Torch", 6)
        restored = _roundtrip(bare_state)
        assert restored.party.active_light is not None
        assert restored.party.active_light.label == "Torch"
        assert restored.party.active_light.turns_remaining == 6

    def test_permanent_light_roundtrips(self, bare_state):
        set_light_source(bare_state, "Continual Light", None)
        restored = _roundtrip(bare_state)
        assert restored.party.active_light.turns_remaining is None

    def test_dungeon_roundtrips(self, bare_state):
        room = Room(name="Vault", description="Dark.")
        feat = RoomFeature(name="Chest", description="Locked.", state="locked")
        exit_ = Exit(label="north", description="Stone arch.", door_state=DoorState.OPEN)
        room.features.append(feat)
        room.exits.append(exit_)
        register_room(bare_state, room)
        restored = _roundtrip(bare_state)
        assert len(restored.dungeon.rooms) == 1
        r_room = list(restored.dungeon.rooms.values())[0]
        assert r_room.name == "Vault"
        assert r_room.features[0].state == "locked"
        assert r_room.exits[0].label == "north"

    def test_say_log_roundtrips(self, active_state):
        from engine import say
        say(active_state, "Aldric", "Hello!")
        restored = _roundtrip(active_state)
        assert "Hello!" in restored.say_log[0]

    def test_default_turn_hours_preserved(self, bare_state):
        bare_state.default_turn_hours = 48.0
        restored = _roundtrip(bare_state)
        assert restored.default_turn_hours == 48.0

    def test_none_dungeon_roundtrips(self, bare_state):
        assert bare_state.dungeon is None
        restored = _roundtrip(bare_state)
        assert restored.dungeon is None

    def test_character_uuid_keys_preserved(self, state_with_fighter):
        orig_ids = set(state_with_fighter.characters.keys())
        restored = _roundtrip(state_with_fighter)
        rest_ids = set(restored.characters.keys())
        assert orig_ids == rest_ids

    def test_character_status_preserved(self, state_with_fighter):
        char_id = list(state_with_fighter.characters.keys())[0]
        state_with_fighter.characters[char_id].status = CharacterStatus.PETRIFIED
        restored = _roundtrip(state_with_fighter)
        rest_char = list(restored.characters.values())[0]
        assert rest_char.status == CharacterStatus.PETRIFIED

    def test_multiple_characters_roundtrip(self, party_state):
        restored = _roundtrip(party_state)
        assert len(restored.characters) == 3
        names = {c.name for c in restored.characters.values()}
        assert "Aldric" in names
        assert "Mira" in names


# ---------------------------------------------------------------------------
# Dungeon file format
# ---------------------------------------------------------------------------

class TestDungeonFileFormat:
    def _make_dungeon(self):
        room = Room(name="Entry Hall", description="Dusty.")
        d = Dungeon(name="Keep of Sorrows")
        d.rooms[room.room_id] = room
        d.entrance_id = room.room_id
        return d

    def test_serialize_produces_valid_json(self):
        d = self._make_dungeon()
        json_str = serialize_dungeon_file(d)
        doc = json.loads(json_str)
        assert doc["format"] == "adbx-dungeon"
        assert doc["version"] == 1
        assert "dungeon" in doc

    def test_roundtrip_preserves_dungeon(self):
        d = self._make_dungeon()
        restored = deserialize_dungeon_file(serialize_dungeon_file(d))
        assert restored.name == "Keep of Sorrows"
        assert len(restored.rooms) == 1
        room = list(restored.rooms.values())[0]
        assert room.name == "Entry Hall"

    def test_entrance_id_preserved(self):
        d = self._make_dungeon()
        restored = deserialize_dungeon_file(serialize_dungeon_file(d))
        assert restored.entrance_id == d.entrance_id

    def test_wrong_format_sentinel_rejected(self):
        doc = {"format": "wrong", "version": 1, "dungeon": {}}
        with pytest.raises(ValueError, match="format"):
            deserialize_dungeon_file(json.dumps(doc))

    def test_wrong_version_rejected(self):
        d = self._make_dungeon()
        doc = json.loads(serialize_dungeon_file(d))
        doc["version"] = 99
        with pytest.raises(ValueError, match="version"):
            deserialize_dungeon_file(json.dumps(doc))

    def test_missing_dungeon_key_rejected(self):
        doc = {"format": "adbx-dungeon", "version": 1}
        with pytest.raises(ValueError, match="Missing"):
            deserialize_dungeon_file(json.dumps(doc))

    def test_invalid_json_rejected(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            deserialize_dungeon_file("this is not json{{{")

    def test_multi_room_dungeon_roundtrips(self):
        d = Dungeon(name="Labyrinth")
        for i in range(5):
            room = Room(name=f"Room {i}", description=f"Desc {i}.")
            d.rooms[room.room_id] = room
        restored = deserialize_dungeon_file(serialize_dungeon_file(d))
        assert len(restored.rooms) == 5
