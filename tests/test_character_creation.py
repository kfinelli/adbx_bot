"""
test_character_creation.py — Character creation tests for the Azure ruleset.
"""

from engine import create_character, roll_stats
from engine.azure_engine import POWER_LEVEL
from models import AzureStats, CharacterClass, CharacterStatus, GameState, Party


class TestRollStats:
    def test_returns_four_azure_keys(self):
        stats = roll_stats()
        assert set(stats.keys()) == {"physique", "finesse", "reason", "savvy"}

    def test_all_values_are_scaled_integers(self):
        for _ in range(20):
            stats = roll_stats()
            for key, val in stats.items():
                assert isinstance(val, int), f"{key}={val} is not an int"
                # Azure formula: 2d(4*PL) - 5*PL, range -500 to +300
                assert -500 <= val <= 300, f"{key}={val} out of expected range"


class TestCreateCharacter:
    def test_basic_creation_succeeds(self, bare_state):
        result = create_character(
            bare_state, "Aldric", CharacterClass.KNIGHT, "", owner_id="u1"
        )
        assert result.ok
        assert len(bare_state.characters) == 1

    def test_character_added_to_party(self, bare_state):
        create_character(bare_state, "Aldric", CharacterClass.KNIGHT, "", owner_id="u1")
        char_id = list(bare_state.characters.keys())[0]
        assert char_id in bare_state.party.member_ids

    def test_empty_name_rejected(self, bare_state):
        result = create_character(bare_state, "   ", CharacterClass.KNIGHT, "")
        assert not result.ok
        assert "empty" in result.error.lower()

    def test_hp_scaled_by_power_level(self, bare_state):
        """HP at level 1 = hit_die * POWER_LEVEL (full HP, JRPG-style)."""
        result = create_character(bare_state, "Aldric", CharacterClass.KNIGHT, "")
        assert result.ok
        char = list(bare_state.characters.values())[0]
        # Knight hit_die = 12 → hp_max = 12 * 100 = 1200
        assert char.hp_max == 12 * POWER_LEVEL
        assert char.hp_current == char.hp_max

    def test_hp_minimum_one(self, bare_state):
        """HP must be at least 1 regardless of stats."""
        scores = AzureStats()   # all stats zero
        result = create_character(
            bare_state, "Frail", CharacterClass.KNIGHT, "",
            ability_scores=scores
        )
        assert result.ok
        char = list(bare_state.characters.values())[0]
        assert char.hp_current >= 1
        assert char.hp_max >= 1

    def test_initial_status_active(self, bare_state):
        create_character(bare_state, "Aldric", CharacterClass.KNIGHT, "")
        char = list(bare_state.characters.values())[0]
        assert char.status == CharacterStatus.ACTIVE

    def test_no_spellbook_on_creation(self, bare_state):
        """Spells are granted via skill progression, not at creation."""
        create_character(bare_state, "Mira", CharacterClass.MAGE, "")
        char = list(bare_state.characters.values())[0]
        assert char.spellbook is None

    def test_inventory_empty_on_creation(self, bare_state):
        """Items are assigned separately; no starting pack."""
        create_character(bare_state, "Aldric", CharacterClass.KNIGHT, "")
        char = list(bare_state.characters.values())[0]
        assert char.inventory == []

    def test_base_save_scaled_by_power_level(self, bare_state):
        """base_save stored as job.baseSave * POWER_LEVEL."""
        create_character(bare_state, "Aldric", CharacterClass.KNIGHT, "")
        char = list(bare_state.characters.values())[0]
        # Knight base_save = 4 → stored as 400
        assert char.saving_throws.get("save") == 4 * POWER_LEVEL

    def test_prerolled_stats_used(self, bare_state):
        scores = AzureStats(physique=300, finesse=200, reason=100, savvy=50)
        create_character(
            bare_state, "Demigod", CharacterClass.KNIGHT, "",
            ability_scores=scores
        )
        char = list(bare_state.characters.values())[0]
        assert char.ability_scores.physique == 300
        assert char.ability_scores.finesse  == 200

    def test_prerolled_stats_dict_used(self, bare_state):
        stats = {"physique": 300, "finesse": 200, "reason": 100, "savvy": 50}
        create_character(
            bare_state, "Sturdy", CharacterClass.KNIGHT, "",
            prerolled_stats=stats
        )
        char = list(bare_state.characters.values())[0]
        assert char.ability_scores.physique == 300
        assert char.ability_scores.finesse  == 200

    def test_all_jobs_can_be_created(self, bare_state):
        """Every CharacterClass should produce a valid character."""
        for cls in CharacterClass:
            s = GameState(platform_channel_id="ch", dm_user_id="dm")
            s.party = Party(name="P")
            result = create_character(s, f"Test {cls.value}", cls, "")
            assert result.ok, f"Job {cls.value} failed: {result.error}"

    def test_owner_id_stored(self, bare_state):
        create_character(
            bare_state, "Aldric", CharacterClass.KNIGHT, "", owner_id="discord_123"
        )
        char = list(bare_state.characters.values())[0]
        assert char.owner_id == "discord_123"

    def test_hit_die_varies_by_job(self, bare_state):
        """Different jobs produce different HP totals."""
        knight_state = GameState(platform_channel_id="ch1", dm_user_id="dm")
        knight_state.party = Party(name="P")
        create_character(knight_state, "K", CharacterClass.KNIGHT, "")
        knight_hp = list(knight_state.characters.values())[0].hp_max

        mage_state = GameState(platform_channel_id="ch2", dm_user_id="dm")
        mage_state.party = Party(name="P")
        create_character(mage_state, "M", CharacterClass.MAGE, "")
        mage_hp = list(mage_state.characters.values())[0].hp_max

        # Knight (d12) should have more HP than Mage (d4)
        assert knight_hp > mage_hp
