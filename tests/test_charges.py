"""
tests/test_charges.py — ChargeWeapon / UtilitySpell recharge logic.

Covers:
  - exit_rounds() auto-restores ENCOUNTER-period spells
  - exit_rounds() does NOT restore DAY-period spells
  - adjust_spell_charges() clamps correctly
  - recharge_day_spells() restores only DAY spells
  - Charge state survives persistence round-trip
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    adjust_spell_charges,
    create_character,
    enter_rounds,
    exit_rounds,
    give_item,
    recharge_day_spells,
    start_session,
)
from engine.azure_constants import RechargePeriod
from engine.data_loader import ITEM_REGISTRY
from engine.item import ChargeWeapon, UtilitySpell
from models import CharacterClass, GameState, InventoryItem, Party, SessionMode
from persistence import Database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state() -> GameState:
    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Mira", CharacterClass.MAGE, "Pack A", owner_id="u1")
    start_session(state)
    return state


def _char(state: GameState):
    return next(iter(state.characters.values()))


def _find_encounter_spell():
    """Return an item_id for an ENCOUNTER-period ChargeWeapon in the registry."""
    for iid, defn in ITEM_REGISTRY.items():
        if (isinstance(defn, ChargeWeapon)
                and defn.rechargePeriod == RechargePeriod.ENCOUNTER
                and defn.maxCharges > 0):
            return iid
    return None


def _find_day_spell():
    """Return an item_id for a DAY-period ChargeWeapon in the registry."""
    for iid, defn in ITEM_REGISTRY.items():
        if (isinstance(defn, ChargeWeapon)
                and defn.rechargePeriod == RechargePeriod.DAY
                and defn.maxCharges > 0):
            return iid
    return None


# ---------------------------------------------------------------------------
# Encounter recharge via exit_rounds
# ---------------------------------------------------------------------------

class TestEncounterRecharge:

    def test_encounter_spell_restored_on_exit_rounds(self):
        spell_id = _find_encounter_spell()
        if spell_id is None:
            pytest.skip("No ENCOUNTER ChargeWeapon in registry.")

        state = _make_state()
        char = _char(state)
        give_item(state, char.character_id, spell_id)

        # Drain all charges manually.
        inv = next(i for i in char.inventory if i.item_id == spell_id)
        inv.charges = 0

        enter_rounds(state)
        exit_rounds(state)

        inv = next(i for i in char.inventory if i.item_id == spell_id)
        defn = ITEM_REGISTRY[spell_id]
        assert inv.charges == defn.maxCharges

    def test_day_spell_not_restored_on_exit_rounds(self):
        spell_id = _find_day_spell()
        if spell_id is None:
            pytest.skip("No DAY ChargeWeapon in registry.")

        state = _make_state()
        char = _char(state)
        give_item(state, char.character_id, spell_id)

        inv = next(i for i in char.inventory if i.item_id == spell_id)
        inv.charges = 0

        enter_rounds(state)
        exit_rounds(state)

        inv = next(i for i in char.inventory if i.item_id == spell_id)
        assert inv.charges == 0


# ---------------------------------------------------------------------------
# adjust_spell_charges
# ---------------------------------------------------------------------------

class TestAdjustSpellCharges:

    def _setup_spell(self, max_charges: int = 3, recharge: RechargePeriod = RechargePeriod.DAY):
        """Directly inject an InventoryItem with known charge data into a character."""
        state = _make_state()
        char = _char(state)

        # Find any real ChargeWeapon from registry to use as definition.
        spell_id = None
        for iid, defn in ITEM_REGISTRY.items():
            if (isinstance(defn, ChargeWeapon)
                    and defn.maxCharges == max_charges
                    and defn.rechargePeriod == recharge):
                spell_id = iid
                break

        if spell_id is None:
            # Fall back to any DAY spell and patch our test around its real maxCharges.
            spell_id = _find_day_spell()
            if spell_id is None:
                pytest.skip("No suitable ChargeWeapon in registry.")

        give_item(state, char.character_id, spell_id)
        inv = next(i for i in char.inventory if i.item_id == spell_id)
        defn = ITEM_REGISTRY[spell_id]
        return state, char, inv, defn

    def test_positive_delta_adds_charges(self):
        state, char, inv, defn = self._setup_spell()
        inv.charges = 0
        result = adjust_spell_charges(state, char.character_id, defn.item_id, delta=1)
        assert result.ok
        inv = next(i for i in char.inventory if i.item_id == defn.item_id)
        assert inv.charges == 1

    def test_negative_delta_removes_charges(self):
        state, char, inv, defn = self._setup_spell()
        inv.charges = defn.maxCharges
        result = adjust_spell_charges(state, char.character_id, defn.item_id, delta=-1)
        assert result.ok
        inv = next(i for i in char.inventory if i.item_id == defn.item_id)
        assert inv.charges == defn.maxCharges - 1

    def test_clamps_above_max(self):
        state, char, inv, defn = self._setup_spell()
        inv.charges = defn.maxCharges
        adjust_spell_charges(state, char.character_id, defn.item_id, delta=9999)
        inv = next(i for i in char.inventory if i.item_id == defn.item_id)
        assert inv.charges == defn.maxCharges

    def test_clamps_below_zero(self):
        state, char, inv, defn = self._setup_spell()
        inv.charges = 0
        adjust_spell_charges(state, char.character_id, defn.item_id, delta=-9999)
        inv = next(i for i in char.inventory if i.item_id == defn.item_id)
        assert inv.charges == 0

    def test_infinite_spell_rejected(self):
        state = _make_state()
        char = _char(state)
        # Find an infinite-charge spell.
        spell_id = None
        for iid, defn in ITEM_REGISTRY.items():
            if isinstance(defn, ChargeWeapon) and defn.maxCharges < 0:
                spell_id = iid
                break
        if spell_id is None:
            pytest.skip("No INFINITE ChargeWeapon in registry.")
        give_item(state, char.character_id, spell_id)
        result = adjust_spell_charges(state, char.character_id, spell_id, delta=1)
        assert not result.ok


# ---------------------------------------------------------------------------
# recharge_day_spells
# ---------------------------------------------------------------------------

class TestRechargeDaySpells:

    def test_recharges_only_day_spells(self):
        enc_id = _find_encounter_spell()
        day_id = _find_day_spell()
        if enc_id is None or day_id is None:
            pytest.skip("Need both ENCOUNTER and DAY spells in registry.")

        state = _make_state()
        char = _char(state)
        give_item(state, char.character_id, enc_id)
        give_item(state, char.character_id, day_id)

        # Drain both.
        for inv in char.inventory:
            if inv.item_id in (enc_id, day_id):
                inv.charges = 0

        result = recharge_day_spells(state, char.character_id)
        assert result.ok

        enc_inv = next(i for i in char.inventory if i.item_id == enc_id)
        day_inv = next(i for i in char.inventory if i.item_id == day_id)
        assert enc_inv.charges == 0
        assert day_inv.charges == ITEM_REGISTRY[day_id].maxCharges

    def test_returns_ok_when_no_day_spells(self):
        state = _make_state()
        char = _char(state)
        result = recharge_day_spells(state, char.character_id)
        assert result.ok


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestChargePersistence:

    def test_charges_survive_save_and_load(self):
        spell_id = _find_day_spell()
        if spell_id is None:
            pytest.skip("No DAY ChargeWeapon in registry.")

        state = _make_state()
        char = _char(state)
        give_item(state, char.character_id, spell_id)

        inv = next(i for i in char.inventory if i.item_id == spell_id)
        inv.charges = 1  # deliberately partial

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.save(state)
            loaded = db.load("ch")
            db.close()
        finally:
            os.unlink(db_path)

        assert loaded is not None
        loaded_char = next(iter(loaded.characters.values()))
        loaded_inv = next((i for i in loaded_char.inventory if i.item_id == spell_id), None)
        assert loaded_inv is not None
        assert loaded_inv.charges == 1
