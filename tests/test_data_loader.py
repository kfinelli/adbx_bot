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
    "requires_target":      "enemies",
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
    "hit_die":      "12d100",
    "base_save":    4,
    "primary_stat": "PHY",
    "max_level":    5,
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
        assert a.requires_target == "enemies"
        assert a.requires_destination is False
        tags = [e["tag"] if isinstance(e, dict) else e for e in a.effect_tags]
        assert "melee_attack" in tags
        assert "check_death" in tags

    def test_action_move_values(self):
        a = ACTION_REGISTRY["move"]
        assert a.action_type == "move"
        assert a.requires_destination is True
        assert a.requires_target == "none"
        tags = [e["tag"] if isinstance(e, dict) else e for e in a.effect_tags]
        assert "move_to_band" in tags

    def test_action_affect_values(self):
        a = ACTION_REGISTRY["affect"]
        assert a.action_type == "affect"
        assert a.requires_target == "none"
        assert a.requires_destination is False
        assert a.effect_tags == []

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
            assert cd["KNIGHT"].hit_die == "12d100"
            assert cd["KNIGHT"].primary_stat == "PHY"
            assert isinstance(cd["KNIGHT"], JobDef)

    def test_job_skills_loaded_from_job_file(self):
        """Skills in a job's 'skills' array are loaded into JobDef.skills."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            job = dict(_VALID_JOB)
            job["skills"] = [{"id": "knight_protector", "level": 1}]
            _write(classes / "knight.json", job)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "definitions": {
                    "knight_protector": {
                        "name": "Protector",
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
            job = dict(_VALID_JOB)
            job["skills"] = [{"id": "passive_phy_bonus", "level": 2}]
            _write(classes / "knight.json", job)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "definitions": {
                    "passive_phy_bonus": {
                        "name": "+1 PHY",
                        "type": 5,
                        "stat": "PHY",
                        "bonus": 1,
                        "desc": "Increase Physique by 1!",
                    }
                }
            })
            _, _, cd, _, _ = load_all(Path(tmp))
            skill = cd["KNIGHT"].skills["passive_phy_bonus"]
            assert skill.stat == "PHY"
            assert skill.bonus == 1

    def test_skill_weapon_rank_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            job = dict(_VALID_JOB)
            job["skills"] = [{"id": "martial_expertise_b", "level": 5}]
            _write(classes / "knight.json", job)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "definitions": {
                    "martial_expertise_b": {
                        "name": "Martial Expertise",
                        "type": 6,
                        "rank": "B",
                        "desc": "You can equip gear and weapons with Rank: B",
                    }
                }
            })
            _, _, cd, _, _ = load_all(Path(tmp))
            skill = cd["KNIGHT"].skills["martial_expertise_b"]
            assert skill.rank == "B"
            assert skill.skill_type == SkillType.WEAPON_RANK.value

    def test_shared_skills_merged_into_skill_registry(self):
        """A skill defined once can be granted to multiple jobs."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes, items = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            job2 = dict(_VALID_JOB)
            job2["key"] = "THIEF"
            job2["display_name"] = "Thief"
            job2["primary_stat"] = "FNS"
            knight = dict(_VALID_JOB)
            knight["skills"] = [{"id": "trapwise", "level": 1}]
            job2["skills"] = [{"id": "trapwise", "level": 1}]
            _write(classes / "knight.json", knight)
            _write(classes / "thief.json", job2)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "definitions": {
                    "trapwise": {
                        "name": "Trapwise",
                        "type": 1,
                        "desc": "Locate traps.",
                    }
                }
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
                "definitions": {
                    "bad_rank": {
                        "name": "Bad Rank",
                        "type": 6,
                        "rank": "S",
                        "desc": "Invalid rank.",
                    }
                },
                "grants": {
                    "knight": [{"id": "bad_rank", "level": 1}]
                },
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
            knight = dict(_VALID_JOB)
            knight["skills"] = [{"id": "ghost_strike", "level": 1}]
            _write(classes / "knight.json", knight)
            _write(Path(tmp) / "jobskills" / "skills.json", {
                "definitions": {
                    "ghost_strike": {
                        "name": "Ghost Strike",
                        "type": 2,
                        "action_id": "nonexistent_action",
                        "desc": "Does something.",
                    }
                },
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
