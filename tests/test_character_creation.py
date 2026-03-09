"""
test_character_creation.py — Character creation and stat generation tests.
"""

from engine import create_character, roll_stats
from models import CharacterClass, CharacterStatus, GameState, Party
from tables import EQUIPMENT_PACKAGES


class TestRollStats:
    def test_returns_six_keys(self):
        stats = roll_stats()
        expected = {"strength", "intelligence", "wisdom", "dexterity", "constitution", "charisma"}
        assert set(stats.keys()) == expected

    def test_all_values_in_3d6_range(self):
        for _ in range(20):
            stats = roll_stats()
            for key, val in stats.items():
                assert 3 <= val <= 18, f"{key}={val} out of 3–18 range"


class TestCreateCharacter:
    def test_basic_creation_succeeds(self, bare_state):
        result = create_character(
            bare_state, "Aldric", CharacterClass.FIGHTER, "Pack A", owner_id="u1"
        )
        assert result.ok
        assert len(bare_state.characters) == 1

    def test_character_added_to_party(self, bare_state):
        create_character(
            bare_state, "Aldric", CharacterClass.FIGHTER, "Pack A", owner_id="u1"
        )
        char_id = list(bare_state.characters.keys())[0]
        assert char_id in bare_state.party.member_ids

    def test_empty_name_rejected(self, bare_state):
        result = create_character(
            bare_state, "   ", CharacterClass.FIGHTER, "Pack A"
        )
        assert not result.ok
        assert "empty" in result.error.lower()

    def test_invalid_package_rejected(self, bare_state):
        result = create_character(
            bare_state, "Aldric", CharacterClass.FIGHTER, "NonexistentPack"
        )
        assert not result.ok
        assert "Unknown equipment package" in result.error

    def test_hp_minimum_one(self, bare_state):
        """HP must be at least 1 even with a terrible CON roll."""
        from models import AbilityScores
        scores = AbilityScores(constitution=3)  # worst possible CON modifier
        result = create_character(
            bare_state, "Frail", CharacterClass.FIGHTER, "Pack A",
            ability_scores=scores
        )
        assert result.ok
        char = list(bare_state.characters.values())[0]
        assert char.hp_current >= 1
        assert char.hp_max >= 1

    def test_initial_status_active(self, bare_state):
        create_character(bare_state, "Aldric", CharacterClass.FIGHTER, "Pack A")
        char = list(bare_state.characters.values())[0]
        assert char.status == CharacterStatus.ACTIVE

    def test_spellcaster_has_spellbook(self, bare_state):
        create_character(bare_state, "Mira", CharacterClass.MAGIC_USER, "Pack A")
        char = list(bare_state.characters.values())[0]
        assert char.spellbook is not None

    def test_non_spellcaster_has_no_spellbook(self, bare_state):
        create_character(bare_state, "Aldric", CharacterClass.FIGHTER, "Pack A")
        char = list(bare_state.characters.values())[0]
        assert char.spellbook is None

    def test_prerolled_stats_used(self, bare_state):
        from models import AbilityScores
        scores = AbilityScores(strength=18, intelligence=18, wisdom=18,
                               dexterity=18, constitution=18, charisma=18)
        create_character(
            bare_state, "Demigod", CharacterClass.FIGHTER, "Pack A",
            ability_scores=scores
        )
        char = list(bare_state.characters.values())[0]
        assert char.ability_scores.strength == 18

    def test_prerolled_stats_dict_used(self, bare_state):
        stats = {"strength": 16, "intelligence": 10, "wisdom": 10,
                 "dexterity": 14, "constitution": 12, "charisma": 8}
        create_character(
            bare_state, "Sturdy", CharacterClass.FIGHTER, "Pack A",
            prerolled_stats=stats
        )
        char = list(bare_state.characters.values())[0]
        assert char.ability_scores.strength == 16
        assert char.ability_scores.dexterity == 14

    def test_all_equipment_packages_valid(self, bare_state):
        """Every package in the table should be accepted."""
        for pkg in EQUIPMENT_PACKAGES:
            s = GameState(platform_channel_id="ch", dm_user_id="dm")
            s.party = Party(name="P")
            result = create_character(s, "Tester", CharacterClass.FIGHTER, pkg)
            assert result.ok, f"Package '{pkg}' failed: {result.error}"

    def test_all_classes_can_be_created(self, bare_state):
        """Every CharacterClass should produce a valid character."""
        for cls in CharacterClass:
            s = GameState(platform_channel_id="ch", dm_user_id="dm")
            s.party = Party(name="P")
            result = create_character(s, f"Test {cls.value}", cls, "Pack A")
            assert result.ok, f"Class {cls.value} failed: {result.error}"

    def test_owner_id_stored(self, bare_state):
        create_character(
            bare_state, "Aldric", CharacterClass.FIGHTER, "Pack A", owner_id="discord_123"
        )
        char = list(bare_state.characters.values())[0]
        assert char.owner_id == "discord_123"
