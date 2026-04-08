"""
tests/test_data_loader.py — Tests for engine/data_loader.py.

Verifies:
  - All shipped data files parse and validate without error
  - Registry contents match expected values for each job and action
  - Cross-reference validation catches missing action IDs
  - Malformed JSON files raise ValueError with a useful message
  - load_all() accepts an alternate data_dir for isolated test fixtures
  - Hook object validation (parameterized hooks)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.azure_constants import SkillType
from engine.data_loader import (
    ACTION_REGISTRY,
    CLASS_DEFINITIONS,
    CONDITION_REGISTRY,
    SKILL_REGISTRY,
    ActionDef,
    JobDef,
    SkillDef,
    load_all,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data_dir(tmp: Path) -> tuple[Path, Path, Path, Path]:
    actions    = tmp / "actions"
    conditions = tmp / "conditions"
    classes    = tmp / "classes"
    items      = tmp / "items"
    jobskills  = tmp / "jobskills"
    actions.mkdir()
    conditions.mkdir()
    classes.mkdir()
    items.mkdir()
    jobskills.mkdir()
    return actions, conditions, classes, items


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# Minimal valid action fixture
_VALID_ACTION = {
    "action_id":            "attack",
    "label":                "Attack",
    "button_style":         "danger",
    "action_type":          "attack",
    "description":          "Melee attack.",
    "requires_target":      True,
    "requires_destination": False,
    "range_requirement":    "weapon",
    "effect_tags":          [{"tag": "melee_attack", "dice": "1d6"}],
}

# Minimal valid condition fixture
_VALID_CONDITION = {
    "condition_id":  "poisoned",
    "label":         "Poisoned",
    "duration_type": "rounds",
    "hooks": {
        "on_turn_end": {"tag": "deal_damage", "dice": "1d4", "type": "poison"},
    },
}

# Minimal valid job fixture (Azure schema — no hardcoded ranks or actions)
_VALID_JOB = {
    "key":          "KNIGHT",
    "display_name": "Knight",
    "hit_die":      12,
    "base_save":    4,
    "primary_stat": "PHY",
    "max_level":    5,
}

# Minimal valid jobskills fixture for KNIGHT (used by isolated tests)
_VALID_KNIGHT_SKILLS = {
    "knight": {
        "Attack": {
            "id": "knight_attack",
            "source": "knight",
            "level": 1,
            "type": 2,
            "action_id": "attack",
            "desc": "Basic attack.",
        }
    }
}


# ---------------------------------------------------------------------------
# Tests: production data files load cleanly
# ---------------------------------------------------------------------------

class TestProductionDataFiles:

    def test_all_expected_actions_present(self):
        assert "attack" in ACTION_REGISTRY
        assert "move"   in ACTION_REGISTRY
        assert "affect" in ACTION_REGISTRY

    def test_action_attack_values(self):
        a = ACTION_REGISTRY["attack"]
        assert isinstance(a, ActionDef)
        assert a.label == "Attack"
        assert a.button_style == "danger"
        assert a.action_type == "attack"
        assert a.requires_target is True
        assert a.requires_destination is False
        tags = [e["tag"] if isinstance(e, dict) else e for e in a.effect_tags]
        assert "melee_attack" in tags
        assert "check_death" in tags

    def test_action_move_values(self):
        a = ACTION_REGISTRY["move"]
        assert a.action_type == "move"
        assert a.requires_destination is True
        assert a.requires_target is False
        tags = [e["tag"] if isinstance(e, dict) else e for e in a.effect_tags]
        assert "move_to_band" in tags

    def test_action_affect_values(self):
        a = ACTION_REGISTRY["affect"]
        assert a.action_type == "affect"
        assert a.requires_target is False
        assert a.requires_destination is False
        assert a.effect_tags == []

    def test_all_expected_jobs_present(self):
        expected = {"KNIGHT", "THIEF", "MAGE", "DILETTANTE"}
        assert expected == set(CLASS_DEFINITIONS.keys())

    def test_job_knight_values(self):
        j = CLASS_DEFINITIONS["KNIGHT"]
        assert isinstance(j, JobDef)
        assert j.display_name == "Knight"
        assert j.hit_die == 12
        assert j.base_save == 4
        assert j.primary_stat == "PHY"
        assert j.max_level == 5
        # Actions come from COMBAT_ACTION skills
        action_ids = {s.action_id for s in j.skills.values() if s.action_id}
        assert "aggrieve" in action_ids
        assert "move" not in action_ids  # Move is a top-level button, not from a skill
        assert "affect" in action_ids
        # Starting weapon rank comes from WEAPON_RANK skill at level 1
        weapon_rank_skills = [s for s in j.skills.values() if s.skill_type == SkillType.WEAPON_RANK.value and s.level == 1]
        assert any(s.rank == "C" for s in weapon_rank_skills)

    def test_job_thief_values(self):
        j = CLASS_DEFINITIONS["THIEF"]
        assert j.display_name == "Thief"
        assert j.hit_die == 6
        assert j.base_save == 2
        assert j.primary_stat == "FNS"
        action_ids = {s.action_id for s in j.skills.values() if s.action_id}
        assert "assail" in action_ids

    def test_job_mage_values(self):
        j = CLASS_DEFINITIONS["MAGE"]
        assert j.display_name == "Mage"
        assert j.hit_die == 4
        assert j.primary_stat == "RSN"

    def test_job_dilettante_values(self):
        j = CLASS_DEFINITIONS["DILETTANTE"]
        assert j.display_name == "Dilettante"
        assert j.primary_stat == "SVY"

    def test_condition_registry_has_expected_conditions(self):
        for cid in (
            "poisoned", "stunned", "strengthened", "entangled", "absconding",
            "abdication-immunity", "undefended", "abjuring",
        ):
            assert cid in CONDITION_REGISTRY

    def test_all_classes_have_base_actions(self):
        expected = {"advance", "abdicate", "aggrieve", "assail", "abjure", "abscond", "affect"}
        for key, job_def in CLASS_DEFINITIONS.items():
            action_ids = {s.action_id for s in job_def.skills.values() if s.action_id}
            assert expected <= action_ids, (
                f"Class {key} missing base actions: {expected - action_ids}"
            )

    def test_new_actions_exist_in_registry(self):
        for action_id in ("aggrieve", "advance", "abdicate", "assail", "abjure"):
            assert action_id in ACTION_REGISTRY, f"Missing action: {action_id}"

    def test_all_job_skill_action_ids_exist_in_registry(self):
        """Every action_id referenced by a job skill must exist in ACTION_REGISTRY."""
        for key, job_def in CLASS_DEFINITIONS.items():
            for skill in job_def.skills.values():
                if skill.action_id is not None:
                    assert skill.action_id in ACTION_REGISTRY, (
                        f"Job {key} skill '{skill.name}' references action "
                        f"'{skill.action_id}' which is not in ACTION_REGISTRY"
                    )

    def test_skill_registry_is_dict(self):
        """SKILL_REGISTRY loads without error (empty until _skills.json files added)."""
        assert isinstance(SKILL_REGISTRY, dict)


# ---------------------------------------------------------------------------
# Tests: load_all() with custom temp directories
# ---------------------------------------------------------------------------

class TestLoadAllIsolated:

    def test_empty_dirs_return_empty_registries(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_data_dir(Path(tmp))
            ar, cr, cd, sr, it = load_all(Path(tmp))
            assert ar == {}
            assert cr == {}
            assert cd == {}
            assert sr == {}
            assert it == {}

    def test_valid_action_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            ar, _, _, _, _ = load_all(Path(tmp))
            assert "attack" in ar
            assert ar["attack"].label == "Attack"

    def test_valid_condition_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(conditions / "poisoned.json", _VALID_CONDITION)
            _, cr, _, _, _ = load_all(Path(tmp))
            assert "poisoned" in cr
            assert cr["poisoned"].duration_type == "rounds"
            entry = cr["poisoned"].hooks["on_turn_end"]
            assert isinstance(entry, dict)
            assert entry["tag"] == "deal_damage"

    def test_valid_job_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "knight.json", _VALID_JOB)
            _, _, cd, _, _ = load_all(Path(tmp))
            assert "KNIGHT" in cd
            assert cd["KNIGHT"].hit_die == 12
            assert cd["KNIGHT"].primary_stat == "PHY"
            assert isinstance(cd["KNIGHT"], JobDef)

    def test_job_skills_loaded_from_skills_json(self):
        """Skills in jobskills/skills.json are loaded into the JobDef.skills dict."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "knight.json", _VALID_JOB)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "knight": {
                    "Protector": {
                        "id": "knight_protector",
                        "source": "knight",
                        "level": 1,
                        "type": 4,
                        "desc": "Give +1 DEF to one other party member.",
                    }
                }
            })
            _, _, cd, sr, _ = load_all(Path(tmp))
            assert "knight_protector" in cd["KNIGHT"].skills
            assert "knight_protector" in sr
            skill = cd["KNIGHT"].skills["knight_protector"]
            assert isinstance(skill, SkillDef)
            assert skill.name == "Protector"
            assert skill.source == "knight"
            assert skill.skill_type == SkillType.FREE_ACTION.value

    def test_skill_passive_bonus_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "knight.json", _VALID_JOB)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "knight": {
                    "+1 PHY": {
                        "id": "knight_phyBonus",
                        "source": "knight",
                        "level": 2,
                        "type": 5,
                        "stat": "PHY",
                        "bonus": 1,
                        "desc": "Increase Physique by 1!",
                    }
                }
            })
            _, _, cd, _, _ = load_all(Path(tmp))
            skill = cd["KNIGHT"].skills["knight_phyBonus"]
            assert skill.stat == "PHY"
            assert skill.bonus == 1

    def test_skill_weapon_rank_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "knight.json", _VALID_JOB)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "knight": {
                    "Martial Expertise": {
                        "id": "martial_iii",
                        "source": "knight",
                        "level": 5,
                        "type": 6,
                        "rank": "B",
                        "desc": "You can equip gear and weapons with Rank: B",
                    }
                }
            })
            _, _, cd, _, _ = load_all(Path(tmp))
            skill = cd["KNIGHT"].skills["martial_iii"]
            assert skill.rank == "B"
            assert skill.skill_type == SkillType.WEAPON_RANK.value

    def test_shared_skills_merged_into_skill_registry(self):
        """A skill id shared by two jobs appears in SKILL_REGISTRY and both JobDefs."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            job2 = dict(_VALID_JOB)
            job2["key"] = "THIEF"
            job2["display_name"] = "Thief"
            job2["primary_stat"] = "FNS"
            _write(classes / "knight.json", _VALID_JOB)
            _write(classes / "thief.json", job2)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "knight": {
                    "Trapwise": {
                        "id": "trapwise",
                        "source": "knight",
                        "level": 1,
                        "type": 1,
                        "desc": "Locate traps.",
                    }
                },
                "thief": {
                    "Trapwise": {
                        "id": "trapwise",
                        "source": "thief",
                        "level": 1,
                        "type": 1,
                        "desc": "Locate traps.",
                    }
                },
            })
            _, _, cd, sr, _ = load_all(Path(tmp))
            assert "trapwise" in sr
            assert "trapwise" in cd["KNIGHT"].skills
            assert "trapwise" in cd["THIEF"].skills

    def test_load_item_registry(self):
        """Test loading items from the item registry"""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items_dir = _make_data_dir(Path(tmp))
            # Create a minimal items.json file for testing (normalised format)
            test_items = {
                "Weapon": [{
                    "item_id": "test_sword",
                    "item_type": "weapon",
                    "name": "Test Sword",
                    "description": "A test weapon",
                    "is_light": False,
                    "rank": "C",
                    "type": "Sword",
                    "stat": "physique",
                    "damage": "1d8",
                    "range": 0,
                    "tags": [],
                    "other_abilities": "",
                    "held_status": "",
                    "attack_status": "",
                    "purchaseable": False,
                    "price": 0
                }],
                "Body": [{
                    "item_id": "test_armor",
                    "item_type": "gear",
                    "name": "Test Armor",
                    "description": "A test armor",
                    "is_light": False,
                    "rank": "D",
                    "slot": "body",
                    "health": 1,
                    "defense": 0,
                    "resistance": 0,
                    "tags": [],
                    "other_abilities": "",
                    "held_status": "",
                    "attack_status": "",
                    "purchaseable": False,
                    "price": 0
                }],
                "Magic": [{
                    "item_id": "test_wand",
                    "item_type": "charge_weapon",
                    "name": "Test Wand",
                    "description": "A test charge weapon",
                    "is_light": False,
                    "rank": "V",
                    "type": "Fire",
                    "stat": "reason",
                    "damage": "1d6",
                    "range": 2,
                    "max_charges": -1,
                    "charges": -1,
                    "recharge_period": "infinite",
                    "destroy_on_empty": False,
                    "tags": ["Black"],
                    "other_abilities": "",
                    "held_status": "",
                    "attack_status": "",
                    "purchaseable": False,
                    "price": 0
                }]
            }
            _write(items_dir / "items.json", test_items)
            _, _, _, _, it = load_all(Path(tmp))

            # Verify items were loaded
            assert len(it) == 3
            assert "test_sword" in it
            assert "test_armor" in it
            assert "test_wand" in it

            # Verify weapon properties
            sword = it["test_sword"]
            assert sword.name == "Test Sword"
            assert sword.rank == "C"
            assert sword.type == "Sword"

            # Verify gear properties
            armor = it["test_armor"]
            assert armor.name == "Test Armor"
            assert armor.slot == "body"

            # Verify charge weapon properties
            wand = it["test_wand"]
            assert wand.name == "Test Wand"
            assert wand.rank == "V"

    def test_load_item_registry_empty(self):
        """Test loading items from an empty items.json"""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items_dir = _make_data_dir(Path(tmp))
            _write(items_dir / "items.json", {})
            _, _, _, _, it = load_all(Path(tmp))
            assert it == {}

    def test_load_item_registry_duplicate_id_raises_error(self):
        """Test that duplicate item IDs raise a ValueError"""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items_dir = _make_data_dir(Path(tmp))
            test_items = {
                    "Weapon": [
                        {"item_id": "dup_item", "item_type": "weapon", "name": "First",
                         "description": "", "is_light": False, "rank": "E", "type": "Sword",
                         "stat": "physique", "damage": "1d4", "range": 0, "tags": [],
                         "other_abilities": "", "held_status": "", "attack_status": "",
                         "purchaseable": False, "price": 0},
                        ],
                    "Body": [
                        {"item_id": "dup_item", "item_type": "gear", "name": "Second",
                         "description": "", "is_light": False, "rank": "E", "slot": "body",
                         "health": 1, "defense": 0, "resistance": 0, "tags": [],
                         "other_abilities": "", "held_status": "", "attack_status": "",
                         "purchaseable": False, "price": 0},
                        ]
                    }
            _write(items_dir / "items.json", test_items)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised ValueError for duplicate item_id")
            except ValueError as e:
                assert "Duplicate item_id" in str(e)

# ---------------------------------------------------------------------------
# Tests: validation rejects bad data
# ---------------------------------------------------------------------------

class TestValidationErrors:

    def test_action_missing_required_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_ACTION)
            del bad["action_id"]
            _write(actions / "attack.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "missing required keys" in str(e)

    def test_action_id_mismatch_with_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_ACTION)
            bad["action_id"] = "move"
            _write(actions / "attack.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "must match filename stem" in str(e)

    def test_invalid_button_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_ACTION)
            bad["button_style"] = "purple"
            _write(actions / "attack.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "button_style" in str(e)

    def test_invalid_action_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_ACTION)
            bad["action_type"] = "jump"
            _write(actions / "attack.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "action_type" in str(e)

    def test_invalid_duration_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_CONDITION)
            bad["duration_type"] = "forever"
            _write(conditions / "poisoned.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "duration_type" in str(e)

    def test_unknown_hook_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_CONDITION)
            bad["hooks"] = {"on_sneeze": "do_something"}
            _write(conditions / "poisoned.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "hook" in str(e).lower()

    def test_job_missing_required_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_JOB)
            del bad["hit_die"]
            _write(classes / "knight.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "missing required keys" in str(e)

    def test_invalid_primary_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_JOB)
            bad["primary_stat"] = "STRENGTH"
            _write(classes / "knight.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "primary_stat" in str(e)

    def test_invalid_skill_weapon_rank(self):
        """A WEAPON_RANK skill with an invalid rank letter should raise."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(classes / "knight.json", _VALID_JOB)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "knight": {
                    "Bad Rank": {
                        "id": "bad_rank",
                        "source": "knight",
                        "level": 1,
                        "type": 6,
                        "rank": "S",
                        "desc": "Invalid rank.",
                    }
                }
            })
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "rank" in str(e).lower()

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            (actions / "attack.json").write_text("{not valid json", encoding="utf-8")
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "Invalid JSON" in str(e)

    def test_skill_references_unknown_action(self):
        """A COMBAT_ACTION skill referencing a missing action_id should raise."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "knight.json", _VALID_JOB)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "knight": {
                    "Ghost Strike": {
                        "id": "ghost_strike",
                        "source": "knight",
                        "level": 1,
                        "type": 2,
                        "action_id": "nonexistent_action",
                        "desc": "Does something.",
                    }
                }
            })
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "nonexistent_action" in str(e)

    def test_condition_references_unknown_action_in_grants(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            cond = dict(_VALID_CONDITION)
            cond["grants_actions"] = ["ghost_touch"]
            _write(conditions / "poisoned.json", cond)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "ghost_touch" in str(e)

    def test_hook_object_missing_tag_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_CONDITION)
            bad["hooks"] = {"on_turn_end": {"dice": "1d6"}}  # missing "tag"
            _write(conditions / "poisoned.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "tag" in str(e).lower()

    def test_effect_tag_object_missing_tag_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            bad = dict(_VALID_ACTION)
            bad["effect_tags"] = [{"dice": "1d6"}]  # missing "tag"
            _write(actions / "attack.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "tag" in str(e).lower()
