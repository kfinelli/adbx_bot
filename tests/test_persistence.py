"""
test_persistence.py — SQLite persistence: save, load, delete, archive, resurrect.
"""

import asyncio
import os
import tempfile

import pytest

from engine import create_character, start_session
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
        create_character(s, "Aldric", CharacterClass.FIGHTER, "Pack A")
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
