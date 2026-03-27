"""
test_level_up.py — Unit tests for the XP / level-up system.

All tests are pure engine — no Discord or database required.
"""


from engine import award_xp, check_level_up, create_character
from engine.azure_constants import XP_THRESHOLDS
from models import CharacterClass, GameState, Party
from serialization import deserialize_character, serialize_character

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state():
    state = GameState(platform_channel_id="ch_test", dm_user_id="dm_001")
    state.party = Party(name="Test Party")
    return state


def _make_char(state, *, cls=CharacterClass.KNIGHT, name="Hero"):
    result = create_character(state, name, cls, "", owner_id="user_001")
    assert result.ok
    return next(iter(state.characters.values()))


# ---------------------------------------------------------------------------
# Model structure
# ---------------------------------------------------------------------------

class TestJobsInitialization:
    def test_jobs_initialized_on_create(self):
        state = _make_state()
        char = _make_char(state)
        assert char.jobs, "jobs dict should be non-empty"
        assert len(char.jobs) == 1

    def test_jobs_entry_at_level_1(self):
        state = _make_state()
        char = _make_char(state)
        job_exp = next(iter(char.jobs.values()))
        assert job_exp.level == 1

    def test_character_class_property(self):
        state = _make_state()
        char = _make_char(state, cls=CharacterClass.KNIGHT)
        assert char.character_class == CharacterClass.KNIGHT

    def test_character_class_property_mage(self):
        state = _make_state()
        char = _make_char(state, cls=CharacterClass.MAGE)
        assert char.character_class == CharacterClass.MAGE


# ---------------------------------------------------------------------------
# Level-up triggers
# ---------------------------------------------------------------------------

class TestLevelUpTrigger:
    def test_no_level_up_below_threshold(self):
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[1] - 1   # 1 short of level 2
        results = check_level_up(state, char.character_id)
        assert results == []

    def test_level_up_at_threshold(self):
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[1]        # exactly at level 2
        results = check_level_up(state, char.character_id)
        assert len(results) == 1
        assert results[0].new_level == 2
        assert char.level == 2

    def test_level_up_stats_and_hp_increase(self):
        state = _make_state()
        char = _make_char(state)
        hp_before = char.hp_max
        char.experience = XP_THRESHOLDS[1]
        check_level_up(state, char.character_id)
        assert char.hp_max > hp_before
        result = next(iter(state.characters.values()))
        job_exp = next(iter(result.jobs.values()))
        assert job_exp.hp_bonus > 0

    def test_level_up_heals_to_full(self):
        state = _make_state()
        char = _make_char(state)
        char.hp_current = 1                        # wound the character
        char.experience = XP_THRESHOLDS[1]
        check_level_up(state, char.character_id)
        assert char.hp_current == char.hp_max

    def test_multi_level_up_in_one_call(self):
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[2]        # enough for both level 2 and 3
        results = check_level_up(state, char.character_id)
        assert len(results) == 2
        assert char.level == 3

    def test_no_level_up_at_max_level(self):
        state = _make_state()
        char = _make_char(state)
        char.level = 5
        job_exp = next(iter(char.jobs.values()))
        job_exp.level = 5
        char.experience = 999_999
        results = check_level_up(state, char.character_id)
        assert results == []


# ---------------------------------------------------------------------------
# award_xp convenience wrapper
# ---------------------------------------------------------------------------

class TestAwardXp:
    def test_award_xp_increments_experience(self):
        state = _make_state()
        char = _make_char(state)
        result = award_xp(state, char.character_id, 500)
        assert result.ok
        assert char.experience == 500

    def test_award_xp_triggers_level_up(self):
        state = _make_state()
        char = _make_char(state)
        result = award_xp(state, char.character_id, XP_THRESHOLDS[1])
        assert result.ok
        level_ups = result.data
        assert len(level_ups) == 1
        assert char.level == 2

    def test_award_xp_no_level_up_returns_empty_data(self):
        state = _make_state()
        char = _make_char(state)
        result = award_xp(state, char.character_id, 100)
        assert result.data == []

    def test_award_xp_unknown_character(self):
        from uuid import uuid4
        state = _make_state()
        result = award_xp(state, uuid4(), 100)
        assert not result.ok


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_serialize_roundtrip_with_jobs(self):
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[1]
        check_level_up(state, char.character_id)

        data = serialize_character(char)
        assert "jobs" in data
        assert "character_class" not in data

        restored = deserialize_character(data)
        assert restored.level == char.level
        assert restored.character_class == char.character_class
        assert restored.jobs == char.jobs
        assert restored.hp_max == char.hp_max

    def test_deserialize_old_format_migration(self):
        """Old saves with character_class key must migrate cleanly."""
        old_data = {
            "character_id": "00000000-0000-0000-0000-000000000001",
            "owner_id": None,
            "name": "Legacy",
            "character_class": "Knight",
            "level": 2,
            "experience": 2000,
            "ability_scores": {"physique": 0, "finesse": 0, "reason": 0, "savvy": 0},
            "hp_max": 800,
            "hp_current": 800,
            "movement_speed": 40,
            "saving_throws": {},
            "status": "active",
            "status_notes": "",
            "inventory": [],
            "gold": 0,
            "created_at": "2026-01-01T00:00:00",
            "is_pregenerated": False,
        }
        char = deserialize_character(old_data)
        assert char.jobs, "jobs dict must be populated from migration"
        assert char.character_class == CharacterClass.KNIGHT
        assert char.level == 2
        job_exp = next(iter(char.jobs.values()))
        assert job_exp.level == 2
