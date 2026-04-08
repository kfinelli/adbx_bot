"""
tests/test_skills.py — Tests for the job skills system.

Covers:
  - get_active_skills returns skills at or below character's level
  - Equip rank checks are derived from WEAPON_RANK skills
  - Level-up applies PASSIVE_BONUS skills to character stats
  - LevelUpResult includes skills_granted
  - Action buttons include COMBAT_ACTION skills
"""

import pytest

from engine import create_character, equip_item, give_item
from engine.azure_constants import XP_THRESHOLDS, SkillType
from engine.character import CharacterManager
from engine.data_loader import CLASS_DEFINITIONS, ITEM_REGISTRY, SkillDef
from engine.item import ChargeWeapon, Weapon
from models import CharacterClass, GameState, Party

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state():
    state = GameState(platform_channel_id="ch_skills_test", dm_user_id="dm_001")
    state.party = Party(name="Test Party")
    return state


def _make_char(state, cls=CharacterClass.KNIGHT, name="Hero"):
    result = create_character(state, name, cls, "", owner_id="user_001")
    assert result.ok
    return next(iter(state.characters.values()))


def _get_char(state):
    return next(iter(state.characters.values()))


def _level_up_to(state, char_id, target_level):
    """Award enough XP to reach target_level."""
    char = state.characters[char_id]
    if target_level > 1:
        char.experience = XP_THRESHOLDS[target_level - 1]
        from engine import check_level_up
        check_level_up(state, char_id)


# ---------------------------------------------------------------------------
# get_active_skills
# ---------------------------------------------------------------------------

class TestGetActiveSkills:

    def test_level_1_knight_has_level_1_skills(self):
        state = _make_state()
        char = _make_char(state)
        skills = CharacterManager.get_active_skills(char)
        assert len(skills) > 0
        assert all(s.level <= 1 for s in skills)

    def test_level_1_skills_do_not_include_higher_level(self):
        state = _make_state()
        char = _make_char(state)
        skills = CharacterManager.get_active_skills(char)
        # Knight has level-2 +1 PHY skill — should NOT be active at level 1
        level_2_plus = [s for s in skills if s.level > 1]
        assert level_2_plus == [], f"Unexpected higher-level skills: {level_2_plus}"

    def test_level_2_includes_level_2_skills(self):
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[1]
        from engine import check_level_up
        check_level_up(state, char.character_id)
        assert char.level == 2

        skills = CharacterManager.get_active_skills(char)
        levels = {s.level for s in skills}
        assert 2 in levels

    def test_skills_are_skill_def_instances(self):
        state = _make_state()
        char = _make_char(state)
        skills = CharacterManager.get_active_skills(char)
        assert all(isinstance(s, SkillDef) for s in skills)


# ---------------------------------------------------------------------------
# Equip rank enforcement via WEAPON_RANK skills
# ---------------------------------------------------------------------------

class TestEquipRankFromSkills:

    def _find_rank_weapon(self, rank: str):
        """Find a physical weapon at the given rank."""
        _ARCANE = {"V", "W", "X", "Y", "Z"}
        for item_id, defn in ITEM_REGISTRY.items():
            if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon) and defn.rank == rank and defn.rank not in _ARCANE:
                return item_id
        return None

    def test_knight_can_equip_rank_c_weapon_at_level_1(self):
        """Knight gets a rank-C WEAPON_RANK skill at level 1."""
        weapon_id = self._find_rank_weapon("C")
        if weapon_id is None:
            pytest.skip("No rank-C weapon in ITEM_REGISTRY")
        state = _make_state()
        char = _make_char(state)
        give_item(state, char.character_id, weapon_id)
        result = equip_item(state, char.character_id, weapon_id)
        assert result.ok, f"Equip failed: {result.error}"

    def test_knight_cannot_equip_rank_a_weapon_at_level_1(self):
        """Knight has no rank-A WEAPON_RANK skill until a higher level."""
        weapon_id = self._find_rank_weapon("A")
        if weapon_id is None:
            pytest.skip("No rank-A weapon in ITEM_REGISTRY")
        state = _make_state()
        char = _make_char(state)
        give_item(state, char.character_id, weapon_id)
        result = equip_item(state, char.character_id, weapon_id)
        assert not result.ok

    def test_mage_can_equip_rank_v_spell_at_level_1(self):
        """Mage gets arcane_i (rank V) at level 1."""
        arcane_weapon = None
        for item_id, defn in ITEM_REGISTRY.items():
            if isinstance(defn, ChargeWeapon) and defn.rank == "V":
                arcane_weapon = item_id
                break
        if arcane_weapon is None:
            pytest.skip("No rank-V arcane weapon in ITEM_REGISTRY")
        state = _make_state()
        char = _make_char(state, cls=CharacterClass.MAGE)
        give_item(state, char.character_id, arcane_weapon)
        result = equip_item(state, char.character_id, arcane_weapon)
        assert result.ok, f"Mage should equip rank-V spell: {result.error}"

    def test_mage_cannot_equip_rank_w_spell_at_level_1(self):
        """Mage gets rank-W only at level 3."""
        arcane_weapon = None
        for item_id, defn in ITEM_REGISTRY.items():
            if isinstance(defn, ChargeWeapon) and defn.rank == "W":
                arcane_weapon = item_id
                break
        if arcane_weapon is None:
            pytest.skip("No rank-W arcane weapon in ITEM_REGISTRY")
        state = _make_state()
        char = _make_char(state, cls=CharacterClass.MAGE)
        give_item(state, char.character_id, arcane_weapon)
        result = equip_item(state, char.character_id, arcane_weapon)
        assert not result.ok, "Mage should not equip rank-W at level 1"


# ---------------------------------------------------------------------------
# Level-up applies PASSIVE_BONUS skills
# ---------------------------------------------------------------------------

class TestLevelUpSkillEffects:

    def test_level_up_includes_skills_granted(self):
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[1]  # level 2
        from engine import check_level_up
        results = check_level_up(state, char.character_id)
        assert len(results) == 1
        result = results[0]
        assert hasattr(result, "skills_granted")
        # Knight gets +1 PHY at level 2 — skills_granted should be non-empty
        assert len(result.skills_granted) > 0

    def test_passive_bonus_skill_increases_stat(self):
        """Knight +1 PHY at level 2 should raise physique."""
        state = _make_state()
        char = _make_char(state)
        phyBefore = char.ability_scores.physique
        char.experience = XP_THRESHOLDS[1]  # level 2
        from engine import check_level_up
        check_level_up(state, char.character_id)
        # The primary stat gain is random, but PASSIVE_BONUS skill adds its bonus
        phyAfter = char.ability_scores.physique
        assert phyAfter > phyBefore, "Physique should have increased at level-up"

    def test_passive_bonus_stat_changes_reflected_in_result(self):
        """stat_changes in LevelUpResult includes PASSIVE_BONUS contributions."""
        state = _make_state()
        char = _make_char(state)
        char.experience = XP_THRESHOLDS[1]
        from engine import check_level_up
        results = check_level_up(state, char.character_id)
        result = results[0]
        assert "physique" in result.stat_changes
        # Knight gets +1 PHY from skill, plus random primary stat roll
        assert result.stat_changes["physique"] >= 1


# ---------------------------------------------------------------------------
# SKILL_REGISTRY completeness
# ---------------------------------------------------------------------------

class TestSkillRegistry:

    def test_all_jobs_have_skills(self):
        for key, job_def in CLASS_DEFINITIONS.items():
            assert len(job_def.skills) > 0, f"Job {key} has no skills"

    def test_all_jobs_have_weapon_rank_skill_at_level_1(self):
        for key, job_def in CLASS_DEFINITIONS.items():
            level_1_rank_skills = [
                s for s in job_def.skills.values()
                if s.skill_type == SkillType.WEAPON_RANK.value and s.level == 1
            ]
            assert level_1_rank_skills, (
                f"Job {key} has no WEAPON_RANK skill at level 1; "
                f"characters could not equip any items"
            )

    def test_all_jobs_have_combat_action_skills(self):
        for key, job_def in CLASS_DEFINITIONS.items():
            combat_skills = [
                s for s in job_def.skills.values()
                if s.skill_type == SkillType.COMBAT_ACTION.value
            ]
            assert combat_skills, f"Job {key} has no COMBAT_ACTION skills"

    def test_weapon_rank_skills_have_rank_field(self):
        for key, job_def in CLASS_DEFINITIONS.items():
            for skill in job_def.skills.values():
                if skill.skill_type == SkillType.WEAPON_RANK.value:
                    assert skill.rank is not None, (
                        f"Job {key} WEAPON_RANK skill '{skill.name}' has no rank"
                    )

    def test_combat_action_skills_have_action_id(self):
        for key, job_def in CLASS_DEFINITIONS.items():
            for skill in job_def.skills.values():
                if skill.skill_type == SkillType.COMBAT_ACTION.value:
                    assert skill.action_id is not None, (
                        f"Job {key} COMBAT_ACTION skill '{skill.name}' has no action_id"
                    )
