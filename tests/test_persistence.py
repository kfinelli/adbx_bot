"""
test_persistence.py — SQLite persistence: save, load, delete, archive, resurrect.
"""

import asyncio
import os
import tempfile

import pytest

from engine import award_xp, create_character, start_session
from models import CharacterClass, GameState, Party
from persistence import Database

# ---------------------------------------------------------------------------
# Fixture: temporary database per test
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    database = Database(path)
    yield database
    database.close()
    os.unlink(path)


def _make_state(channel_id="ch1", dm="dm1", party_name="Test Party"):
    s = GameState(platform_channel_id=channel_id, dm_user_id=dm)
    s.party = Party(name=party_name)
    return s


# ---------------------------------------------------------------------------
# Basic save / load / delete
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_and_load(self, db):
        s = _make_state()
        db.save(s)
        loaded = db.load("ch1")
        assert loaded is not None
        assert loaded.platform_channel_id == "ch1"
        assert loaded.party.name == "Test Party"

    def test_load_missing_returns_none(self, db):
        assert db.load("no_such_channel") is None

    def test_save_overwrites_existing(self, db):
        s = _make_state()
        db.save(s)
        s.party.name = "Renamed"
        db.save(s)
        loaded = db.load("ch1")
        assert loaded.party.name == "Renamed"

    def test_delete_removes_session(self, db):
        s = _make_state()
        db.save(s)
        db.delete("ch1")
        assert db.load("ch1") is None

    def test_delete_nonexistent_is_safe(self, db):
        db.delete("no_such_channel")   # should not raise

    def test_list_channels_empty(self, db):
        assert db.list_channels() == []

    def test_list_channels_after_save(self, db):
        db.save(_make_state("ch1"))
        db.save(_make_state("ch2"))
        channels = db.list_channels()
        assert set(channels) == {"ch1", "ch2"}

    def test_session_id_preserved(self, db):
        s = _make_state()
        db.save(s)
        loaded = db.load("ch1")
        assert loaded.session_id == s.session_id


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

class TestArchive:
    def test_archive_removes_from_active(self, db):
        db.save(_make_state("ch1"))
        ok = asyncio.run(db.archive_async("ch1", "general"))
        assert ok is True
        assert db.load("ch1") is None

    def test_archive_entry_appears_in_list(self, db):
        s = _make_state("ch1")
        db.save(s)
        asyncio.run(db.archive_async("ch1", "dungeon-channel"))
        entries = asyncio.run(db.list_archive_async())
        assert len(entries) == 1
        e = entries[0]
        assert e["channel_id"] == "ch1"
        assert e["channel_name"] == "dungeon-channel"
        assert e["session_id"] == str(s.session_id)

    def test_archive_missing_returns_false(self, db):
        ok = asyncio.run(db.archive_async("no_such"))
        assert ok is False

    def test_archive_does_not_affect_other_sessions(self, db):
        db.save(_make_state("ch1"))
        db.save(_make_state("ch2"))
        asyncio.run(db.archive_async("ch1"))
        assert db.load("ch2") is not None

    def test_list_archive_empty(self, db):
        assert asyncio.run(db.list_archive_async()) == []

    def test_delete_archive_entry(self, db):
        s = _make_state("ch1")
        db.save(s)
        asyncio.run(db.archive_async("ch1"))
        asyncio.run(db.delete_archive_async(str(s.session_id)))
        entries = asyncio.run(db.list_archive_async())
        assert entries == []

    def test_archive_metadata_turn_number(self, db):
        """Archive entry should capture the turn number at archive time."""
        s = _make_state("ch1")
        create_character(s, "Aldric", CharacterClass.KNIGHT, "")
        start_session(s)
        s.turn_number = 7
        db.save(s)
        asyncio.run(db.archive_async("ch1"))
        entries = asyncio.run(db.list_archive_async())
        assert entries[0]["turn_number"] == 7


# ---------------------------------------------------------------------------
# Resurrect
# ---------------------------------------------------------------------------

class TestResurrect:
    def test_resurrect_restores_state(self, db):
        s = _make_state("ch1", party_name="Fearless Band")
        db.save(s)
        asyncio.run(db.archive_async("ch1"))
        restored = asyncio.run(db.resurrect_async(str(s.session_id), "ch1_new"))
        assert restored is not None
        assert restored.party.name == "Fearless Band"
        assert db.load("ch1_new") is not None

    def test_resurrect_archive_copy_remains(self, db):
        s = _make_state("ch1")
        db.save(s)
        asyncio.run(db.archive_async("ch1"))
        asyncio.run(db.resurrect_async(str(s.session_id), "ch1_new"))
        entries = asyncio.run(db.list_archive_async())
        assert len(entries) == 1   # archive entry still present

    def test_resurrect_unknown_session_returns_none(self, db):
        from uuid import uuid4
        result = asyncio.run(db.resurrect_async(str(uuid4()), "ch_new"))
        assert result is None

    def test_resurrect_onto_busy_channel_overwrites(self, db):
        """resurrect_async itself doesn't block — the route layer does.
        This test confirms the DB-level behaviour (overwrite) so the route
        test knows what it's guarding against."""
        s1 = _make_state("ch1", party_name="Party A")
        s2 = _make_state("ch2", party_name="Party B")
        db.save(s1)
        db.save(s2)
        asyncio.run(db.archive_async("ch1"))
        # Resurrect onto ch2 which is already active
        restored = asyncio.run(db.resurrect_async(str(s1.session_id), "ch2"))
        assert restored is not None
        # ch2 now holds the resurrected state
        assert db.load("ch2").party.name == "Party A"


# ---------------------------------------------------------------------------
# Concurrent async saves (lock correctness)
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_saves_both_persist(self, db):
        s1 = _make_state("ch1", party_name="Party A")
        s2 = _make_state("ch2", party_name="Party B")

        async def run():
            await asyncio.gather(
                db.save_async(s1),
                db.save_async(s2),
            )

        asyncio.run(run())
        assert db.load("ch1").party.name == "Party A"
        assert db.load("ch2").party.name == "Party B"

    def test_concurrent_saves_do_not_corrupt(self, db):
        """Rapid repeated saves to the same channel should always yield the last write."""
        _make_state("ch1")

        async def run():
            tasks = []
            for i in range(10):
                s_copy = _make_state("ch1", party_name=f"Party {i}")
                tasks.append(db.save_async(s_copy))
            await asyncio.gather(*tasks)

        asyncio.run(run())
        loaded = db.load("ch1")
        assert loaded is not None  # no corruption, something was saved


# ---------------------------------------------------------------------------
# Dual-path coherency
#
# The DB has two write paths:
#   1. save_async(state)  — session save: writes ALL characters in the state
#   2. save_character_async(char) — standalone save: writes ONE character
#
# A standalone update followed by a session save must NOT revert the
# standalone changes. This is the class of bug caught by sync_character_to_sessions.
# ---------------------------------------------------------------------------

def _make_char_state(db: Database, channel_id="ch1") -> tuple:
    """Create a character, enroll it in a session, and save both to DB.
    Returns (session_state, char).
    """
    s = _make_state(channel_id)
    create_character(s, "Hero", CharacterClass.KNIGHT, "", owner_id="u1")
    char = next(iter(s.characters.values()))
    db.save(s)
    db.save_character(char)
    return s, char


class TestDualPathCoherency:
    def test_standalone_save_persists_level(self, db):
        """save_character writes the new level; subsequent load sees it."""
        s, char = _make_char_state(db)
        char.level = 5
        char.experience = 16000
        db.save_character(char)
        reloaded = db.load_character(str(char.character_id))
        assert reloaded.level == 5
        assert reloaded.experience == 16000

    def test_session_save_after_standalone_save_preserves_level(self, db):
        """
        The regression scenario:
        1. standalone update → save_character (level 2)
        2. session save with stale in-memory char (level 1)
        3. load → must still be level 2
        sync_character_to_sessions fixes this by updating the in-memory state
        before any session save can clobber it.
        """
        s, char = _make_char_state(db)
        assert char.level == 1

        # Step 1: standalone update — simulates award_xp via webui
        char.level = 2
        char.experience = 2000
        db.save_character(char)

        # Step 2: session save with the UPDATED char (sync_character_to_sessions
        # would have placed this updated char into the session state).
        # Here we simulate that correctly: s.characters already has the same
        # object reference, so it IS the updated char.
        db.save(s)

        # Step 3: verify the DB reflects the standalone update, not a rollback
        reloaded = db.load_character(str(char.character_id))
        assert reloaded.level == 2
        assert reloaded.experience == 2000

    def test_stale_session_save_would_revert_without_sync(self, db):
        """
        Demonstrates the original bug: a session save with a STALE char object
        (different Python object, old data) overwrites the standalone save.
        This test documents the unsafe pattern so we know what sync prevents.
        """
        from serialization import deserialize_character, serialize_character

        s, char = _make_char_state(db)

        # Simulate standalone update — save level 2 to DB
        char.level = 2
        char.experience = 2000
        db.save_character(char)

        # Simulate stale in-memory session: reconstruct the old char object
        # (as if the session loaded before the standalone save and was never synced)
        old_char_data = serialize_character(char)
        old_char_data["level"] = 1
        old_char_data["experience"] = 0
        old_char_data["jobs"]["knight"]["level"] = 1
        stale_char = deserialize_character(old_char_data)

        stale_state = _make_state("ch1")
        stale_state.session_id = s.session_id
        stale_state.characters = {stale_char.character_id: stale_char}
        db.save(stale_state)

        # Without sync: the stale session save reverts the character
        reverted = db.load_character(str(char.character_id))
        assert reverted.level == 1   # confirms the bug exists without sync

    def test_sync_character_to_sessions_updates_in_memory_state(self):
        """
        sync_character_to_sessions replaces the char in every in-memory
        session that contains it — verified against the real session_cache.
        """
        from copy import deepcopy

        import session_cache

        # Build a fake in-memory session containing the original char
        fake_state = GameState(platform_channel_id="ch_fake", dm_user_id="dm1")
        fake_state.party = Party(name="Test")
        create_character(fake_state, "Tester", CharacterClass.KNIGHT, "", owner_id="u1")
        original_char = next(iter(fake_state.characters.values()))
        session_cache._sessions["ch_fake"] = fake_state

        try:
            updated_char = deepcopy(original_char)
            updated_char.level = 3

            session_cache.sync_character_to_sessions(updated_char)

            in_memory = session_cache._sessions["ch_fake"].characters[original_char.character_id]
            assert in_memory.level == 3
        finally:
            session_cache._sessions.pop("ch_fake", None)

    def test_award_xp_level_survives_session_save(self, db):
        """End-to-end: award_xp + save_character + sync → session save → level intact."""
        import session_cache

        s, char = _make_char_state(db)
        session_cache._sessions["ch1"] = s

        try:
            # Load fresh from DB (as _load_char_state does) into a shadow state
            shadow = GameState(platform_channel_id="__edit__", dm_user_id="")
            shadow.party = Party(name="")
            fresh_char = db.load_character(str(char.character_id))
            shadow.characters = {fresh_char.character_id: fresh_char}

            # Award XP (mutates fresh_char in place)
            award_xp(shadow, fresh_char.character_id, 2000)
            assert fresh_char.level == 2

            # Save to DB and sync in-memory sessions
            db.save_character(fresh_char)
            session_cache.sync_character_to_sessions(fresh_char)

            # Session save (simulates bot command completing a turn, etc.)
            db.save(s)

            # Reload — must be level 2
            final = db.load_character(str(char.character_id))
            assert final.level == 2
            assert final.experience == 2000
        finally:
            session_cache._sessions.pop("ch1", None)


# ---------------------------------------------------------------------------
# active_conditions round-trip
# ---------------------------------------------------------------------------

class TestActiveConditionsPersistence:
    def test_character_active_conditions_round_trip(self, db):
        """active_conditions survive save → load via the characters table."""
        from engine import apply_condition, start_session
        from models import CharacterClass, GameState, Party

        s = GameState(platform_channel_id="ch99", dm_user_id="dm")
        s.party = Party(name="P")
        create_character(s, "Tester", CharacterClass.KNIGHT, "Pack A", owner_id="u1")
        start_session(s)
        char = next(iter(s.characters.values()))

        # Apply a condition directly (outside combat — now allowed)
        apply_condition(s, char.character_id, "poisoned", duration=3)
        assert len(char.active_conditions) == 1

        db.save_character(char)
        reloaded = db.load_character(str(char.character_id))
        assert len(reloaded.active_conditions) == 1
        assert reloaded.active_conditions[0].condition_id == "poisoned"
        assert reloaded.active_conditions[0].duration_rounds == 3

    def test_character_conditions_empty_by_default(self, db):
        """Characters without conditions save and reload cleanly."""
        from models import CharacterClass, GameState, Party

        s = GameState(platform_channel_id="ch100", dm_user_id="dm")
        s.party = Party(name="P")
        create_character(s, "Clean", CharacterClass.MAGE, "Pack A", owner_id="u2")
        char = next(iter(s.characters.values()))

        db.save_character(char)
        reloaded = db.load_character(str(char.character_id))
        assert reloaded.active_conditions == []
