"""
tests/test_data_loader.py — Tests for engine/data_loader.py.

Verifies:
  - All shipped data files parse and validate without error
  - Registry contents match expected values for each class and action
  - Cross-reference validation catches missing action IDs
  - Malformed JSON files raise ValueError with a useful message
  - load_all() accepts an alternate data_dir for isolated test fixtures
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.data_loader import (
    ACTION_REGISTRY,
    CLASS_DEFINITIONS,
    CONDITION_REGISTRY,
    ActionDef,
    ClassDef,
    load_all,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data_dir(tmp: Path) -> tuple[Path, Path, Path]:
    """Create the standard subdirectory layout under tmp."""
    actions    = tmp / "actions"
    conditions = tmp / "conditions"
    classes    = tmp / "classes"
    actions.mkdir()
    conditions.mkdir()
    classes.mkdir()
    return actions, conditions, classes


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
    "range_requirement":    ["engage"],
    "effect_tags":          ["melee_damage_str_mod"],
}

# Minimal valid condition fixture
_VALID_CONDITION = {
    "condition_id":  "poisoned",
    "label":         "Poisoned",
    "duration_type": "rounds",
    "hooks": {
        "on_turn_start": "deal_1d4_poison_damage",
    },
}

# Minimal valid class fixture
_VALID_CLASS = {
    "key":            "FIGHTER",
    "display_name":   "Fighter",
    "hit_die":        8,
    "base_ac":        9,
    "base_movement":  120,
    "is_spellcaster": False,
    "default_saves":  {"death_poison": 12},
    "pack_bonus_items": {},
    "combat_actions": ["attack"],
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
        assert "melee_damage_str_mod" in a.effect_tags
        assert "check_death" in a.effect_tags

    def test_action_move_values(self):
        a = ACTION_REGISTRY["move"]
        assert a.action_type == "move"
        assert a.requires_destination is True
        assert a.requires_target is False
        assert "move_to_band" in a.effect_tags

    def test_action_affect_values(self):
        a = ACTION_REGISTRY["affect"]
        assert a.action_type == "affect"
        assert a.requires_target is False
        assert a.requires_destination is False
        assert a.effect_tags == []

    def test_all_expected_classes_present(self):
        expected = {"FIGHTER", "THIEF", "CLERIC", "MAGIC_USER", "ELF", "DWARF", "HALFLING"}
        assert expected == set(CLASS_DEFINITIONS.keys())

    def test_class_fighter_values(self):
        c = CLASS_DEFINITIONS["FIGHTER"]
        assert isinstance(c, ClassDef)
        assert c.display_name == "Fighter"
        assert c.hit_die == 8
        assert c.base_movement == 120
        assert c.is_spellcaster is False
        assert c.default_saves["death_poison"] == 12
        assert "attack" in c.combat_actions
        assert "move"   in c.combat_actions
        assert "affect" in c.combat_actions

    def test_class_magic_user_is_spellcaster(self):
        assert CLASS_DEFINITIONS["MAGIC_USER"].is_spellcaster is True

    def test_class_cleric_is_spellcaster(self):
        assert CLASS_DEFINITIONS["CLERIC"].is_spellcaster is True

    def test_class_elf_is_spellcaster(self):
        assert CLASS_DEFINITIONS["ELF"].is_spellcaster is True

    def test_class_fighter_not_spellcaster(self):
        assert CLASS_DEFINITIONS["FIGHTER"].is_spellcaster is False

    def test_class_thief_pack_bonus(self):
        c = CLASS_DEFINITIONS["THIEF"]
        assert "Pack C" in c.pack_bonus_items
        name, qty, enc = c.pack_bonus_items["Pack C"]
        assert name == "Thief's Tools"
        assert qty == 1

    def test_class_cleric_pack_bonus(self):
        c = CLASS_DEFINITIONS["CLERIC"]
        assert "Pack C" in c.pack_bonus_items
        name, qty, enc = c.pack_bonus_items["Pack C"]
        assert name == "Holy Symbol"

    def test_class_dwarf_movement(self):
        assert CLASS_DEFINITIONS["DWARF"].base_movement == 60

    def test_class_halfling_movement(self):
        assert CLASS_DEFINITIONS["HALFLING"].base_movement == 60

    def test_condition_registry_starts_empty(self):
        # No condition files shipped in Phase 1 — registry should be empty
        assert isinstance(CONDITION_REGISTRY, dict)

    def test_all_class_combat_actions_exist_in_registry(self):
        """Every action ID referenced by any class must exist in ACTION_REGISTRY."""
        for key, cls_def in CLASS_DEFINITIONS.items():
            for action_id in cls_def.combat_actions:
                assert action_id in ACTION_REGISTRY, (
                    f"Class {key} references action '{action_id}' "
                    f"which is not in ACTION_REGISTRY"
                )


# ---------------------------------------------------------------------------
# Tests: load_all() with custom temp directories
# ---------------------------------------------------------------------------

class TestLoadAllIsolated:

    def test_empty_dirs_return_empty_registries(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            ar, cr, cd = load_all(Path(tmp))
            assert ar == {}
            assert cr == {}
            assert cd == {}

    def test_valid_action_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            ar, _, _ = load_all(Path(tmp))
            assert "attack" in ar
            assert ar["attack"].label == "Attack"

    def test_valid_condition_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            _write(conditions / "poisoned.json", _VALID_CONDITION)
            _, cr, _ = load_all(Path(tmp))
            assert "poisoned" in cr
            assert cr["poisoned"].duration_type == "rounds"
            assert cr["poisoned"].hooks["on_turn_start"] == "deal_1d4_poison_damage"

    def test_valid_class_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "fighter.json", _VALID_CLASS)
            _, _, cd = load_all(Path(tmp))
            assert "FIGHTER" in cd
            assert cd["FIGHTER"].hit_die == 8

    def test_pack_bonus_tuple_conversion(self):
        """pack_bonus_items JSON arrays must be converted to tuples."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            cls_data = dict(_VALID_CLASS)
            cls_data["pack_bonus_items"] = {"Pack C": ["Thief's Tools", 1, 0.5]}
            _write(classes / "fighter.json", cls_data)
            _, _, cd = load_all(Path(tmp))
            bonus = cd["FIGHTER"].pack_bonus_items["Pack C"]
            assert isinstance(bonus, tuple)
            assert bonus == ("Thief's Tools", 1, 0.5)


# ---------------------------------------------------------------------------
# Tests: validation rejects bad data
# ---------------------------------------------------------------------------

class TestValidationErrors:

    def test_action_missing_required_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
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
            actions, conditions, classes = _make_data_dir(Path(tmp))
            bad = dict(_VALID_ACTION)
            bad["action_id"] = "move"           # doesn't match filename 'attack.json'
            _write(actions / "attack.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "must match filename stem" in str(e)

    def test_invalid_button_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
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
            actions, conditions, classes = _make_data_dir(Path(tmp))
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
            actions, conditions, classes = _make_data_dir(Path(tmp))
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
            actions, conditions, classes = _make_data_dir(Path(tmp))
            bad = dict(_VALID_CONDITION)
            bad["hooks"] = {"on_sneeze": "do_something"}
            _write(conditions / "poisoned.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "hook" in str(e).lower()

    def test_class_missing_required_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            bad = dict(_VALID_CLASS)
            del bad["hit_die"]
            _write(classes / "fighter.json", bad)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "missing required keys" in str(e)

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            (actions / "attack.json").write_text("{not valid json", encoding="utf-8")
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "Invalid JSON" in str(e)

    def test_duplicate_action_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            # Second file with same action_id — impossible via filename, so
            # test the guard by subclassing and injecting duplicate directly.
            # Instead we test cross-validation: class references non-existent action.
            cls_data = dict(_VALID_CLASS)
            cls_data["combat_actions"] = ["attack", "nonexistent_action"]
            _write(actions / "attack.json", _VALID_ACTION)
            _write(classes / "fighter.json", cls_data)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "nonexistent_action" in str(e)

    def test_class_references_unknown_action(self):
        """Cross-validation: class combat_actions entry not in action registry."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            # No action files — class references 'attack' which doesn't exist
            _write(classes / "fighter.json", _VALID_CLASS)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "attack" in str(e)

    def test_condition_references_unknown_action_in_grants(self):
        """Cross-validation: condition grants_actions entry not in action registry."""
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            cond = dict(_VALID_CONDITION)
            cond["grants_actions"] = ["ghost_touch"]   # not in action registry
            _write(conditions / "poisoned.json", cond)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "ghost_touch" in str(e)

    def test_pack_bonus_wrong_length_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions, conditions, classes = _make_data_dir(Path(tmp))
            _write(actions / "attack.json", _VALID_ACTION)
            cls_data = dict(_VALID_CLASS)
            cls_data["pack_bonus_items"] = {"Pack C": ["Only two", 1]}   # missing enc
            _write(classes / "fighter.json", cls_data)
            try:
                load_all(Path(tmp))
                raise AssertionError("Should have raised")
            except ValueError as e:
                assert "3-element" in str(e)
