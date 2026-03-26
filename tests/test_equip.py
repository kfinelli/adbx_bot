"""
test_equip.py — Tests for the equip / unequip inventory system.

Covers:
  - Equipping weapons into MAIN_HAND
  - Equipping gear into HEAD / LEGS slots
  - Equipping accessories into ACCESSORY1 / ACCESSORY2
  - Auto-slot selection for accessories
  - Unequipping items
  - Displacing an already-equipped item when equipping to a full slot
  - defense / resistance stat derivation from equipped gear
  - Serialization round-trip preserves equipped_slots
  - Error paths: unknown item, non-equipable item, wrong slot
"""

import pytest

from engine import create_character, equip_item, unequip_item
from engine.azure_constants import UI_SLOTS, ItemSlot
from engine.data_loader import ITEM_REGISTRY
from engine.item import Gear, Weapon
from models import (
    CharacterClass,
    InventoryItem,
)
from serialization import deserialize_state, serialize_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_char(state):
    """Return the first (and usually only) character in state."""
    return next(iter(state.characters.values()))


def _add_item(char, item_id: str, quantity: int = 1):
    """Add an InventoryItem to a character by item_id."""
    char.inventory.append(InventoryItem(item_id=item_id, quantity=quantity))


def _find_item_id(item_type, **attrs):
    """
    Return the first item_id in ITEM_REGISTRY whose definition is an instance
    of ``item_type`` and whose attributes match ``attrs``.
    """
    for item_id, defn in ITEM_REGISTRY.items():
        if not isinstance(defn, item_type):
            continue
        if all(getattr(defn, k, None) == v for k, v in attrs.items()):
            return item_id
    return None


def _find_gear_id(slot: str):
    """Return the first Gear item_id with the given .slot string."""
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, Gear) and defn.slot == slot:
            return item_id
    return None


def _find_weapon_id():
    """Return the first Weapon item_id available."""
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, Weapon):
            return item_id
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state_char(bare_state):
    """State with a single Knight character, no inventory."""
    create_character(
        bare_state,
        name="Tess",
        character_class=CharacterClass.KNIGHT,
        equipment_package="",
        owner_id="user_equip_test",
    )
    return bare_state


@pytest.fixture
def weapon_id():
    wid = _find_weapon_id()
    assert wid is not None, "No Weapon found in ITEM_REGISTRY — check items.json"
    return wid


@pytest.fixture
def head_gear_id():
    gid = _find_gear_id("head")
    assert gid is not None, "No head Gear found in ITEM_REGISTRY"
    return gid


@pytest.fixture
def legs_gear_id():
    gid = _find_gear_id("legs")
    assert gid is not None, "No legs Gear found in ITEM_REGISTRY"
    return gid


@pytest.fixture
def body_gear_id():
    gid = _find_gear_id("body")
    assert gid is not None, "No body Gear found in ITEM_REGISTRY"
    return gid


@pytest.fixture
def accessory_id():
    aid = _find_gear_id("accessory")
    assert aid is not None, "No accessory Gear found in ITEM_REGISTRY"
    return aid


# ---------------------------------------------------------------------------
# Equipping weapons
# ---------------------------------------------------------------------------

class TestEquipWeapon:
    def test_equip_weapon_to_main_hand(self, state_char, weapon_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)

        result = equip_item(state_char, char.character_id, weapon_id)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.MAIN_HAND.value] == weapon_id
        inv = next(i for i in char.inventory if i.item_id == weapon_id)
        assert inv.equipped is True

    def test_equip_weapon_explicit_main_hand_slot(self, state_char, weapon_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)

        result = equip_item(state_char, char.character_id, weapon_id, slot=ItemSlot.MAIN_HAND)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.MAIN_HAND.value] == weapon_id

    def test_equip_weapon_wrong_slot_fails(self, state_char, weapon_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)

        result = equip_item(state_char, char.character_id, weapon_id, slot=ItemSlot.HEAD)

        assert not result.ok
        assert char.equipped_slots[ItemSlot.MAIN_HAND.value] is None

    def test_equip_second_weapon_displaces_first(self, state_char):
        """Equipping a new weapon into MAIN_HAND automatically unequips the old one."""
        w1 = _find_weapon_id()
        # Find a *different* weapon if possible
        w2 = None
        for item_id, defn in ITEM_REGISTRY.items():
            if isinstance(defn, Weapon) and item_id != w1:
                w2 = item_id
                break
        if w2 is None:
            pytest.skip("Only one weapon in registry; cannot test displacement")

        char = _get_char(state_char)
        _add_item(char, w1)
        _add_item(char, w2)

        equip_item(state_char, char.character_id, w1)
        equip_item(state_char, char.character_id, w2)

        assert char.equipped_slots[ItemSlot.MAIN_HAND.value] == w2
        old = next(i for i in char.inventory if i.item_id == w1)
        new = next(i for i in char.inventory if i.item_id == w2)
        assert old.equipped is False
        assert new.equipped is True


# ---------------------------------------------------------------------------
# Equipping gear
# ---------------------------------------------------------------------------

class TestEquipGear:
    def test_equip_head_gear(self, state_char, head_gear_id):
        char = _get_char(state_char)
        _add_item(char, head_gear_id)

        result = equip_item(state_char, char.character_id, head_gear_id)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.HEAD.value] == head_gear_id

    def test_equip_legs_gear(self, state_char, legs_gear_id):
        char = _get_char(state_char)
        _add_item(char, legs_gear_id)

        result = equip_item(state_char, char.character_id, legs_gear_id)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.LEGS.value] == legs_gear_id

    def test_equip_body_gear(self, state_char, body_gear_id):
        char = _get_char(state_char)
        _add_item(char, body_gear_id)

        result = equip_item(state_char, char.character_id, body_gear_id)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.BODY.value] == body_gear_id

    def test_equip_gear_wrong_slot_fails(self, state_char, head_gear_id):
        char = _get_char(state_char)
        _add_item(char, head_gear_id)

        result = equip_item(state_char, char.character_id, head_gear_id, slot=ItemSlot.LEGS)

        assert not result.ok

    def test_equip_head_displaces_previous(self, state_char):
        helmets = [
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Gear) and defn.slot == "head"
        ]
        if len(helmets) < 2:
            pytest.skip("Need at least 2 head items to test displacement")

        h1, h2 = helmets[0], helmets[1]
        char = _get_char(state_char)
        _add_item(char, h1)
        _add_item(char, h2)

        equip_item(state_char, char.character_id, h1)
        equip_item(state_char, char.character_id, h2)

        assert char.equipped_slots[ItemSlot.HEAD.value] == h2
        old = next(i for i in char.inventory if i.item_id == h1)
        assert old.equipped is False


# ---------------------------------------------------------------------------
# Equipping accessories
# ---------------------------------------------------------------------------

class TestEquipAccessory:
    def test_equip_accessory_fills_first_slot(self, state_char, accessory_id):
        char = _get_char(state_char)
        _add_item(char, accessory_id)

        result = equip_item(state_char, char.character_id, accessory_id)

        assert result.ok, result.error
        # Should go into ACCESSORY1 (first free slot)
        assert char.equipped_slots[ItemSlot.ACCESSORY1.value] == accessory_id

    def test_equip_two_accessories_fills_both_slots(self, state_char):
        accessories = [
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Gear) and defn.slot == "accessory"
        ]
        if len(accessories) < 2:
            pytest.skip("Need at least 2 accessory items")

        a1, a2 = accessories[0], accessories[1]
        char = _get_char(state_char)
        _add_item(char, a1)
        _add_item(char, a2)

        equip_item(state_char, char.character_id, a1)
        equip_item(state_char, char.character_id, a2)

        assert char.equipped_slots[ItemSlot.ACCESSORY1.value] == a1
        assert char.equipped_slots[ItemSlot.ACCESSORY2.value] == a2

    def test_equip_third_accessory_fails_when_both_slots_full(self, state_char):
        accessories = [
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Gear) and defn.slot == "accessory"
        ]
        if len(accessories) < 3:
            pytest.skip("Need at least 3 accessory items")

        a1, a2, a3 = accessories[0], accessories[1], accessories[2]
        char = _get_char(state_char)
        _add_item(char, a1)
        _add_item(char, a2)
        _add_item(char, a3)

        equip_item(state_char, char.character_id, a1)
        equip_item(state_char, char.character_id, a2)
        result = equip_item(state_char, char.character_id, a3)

        assert not result.ok

    def test_equip_accessory_explicit_slot2(self, state_char, accessory_id):
        char = _get_char(state_char)
        _add_item(char, accessory_id)

        result = equip_item(
            state_char, char.character_id, accessory_id, slot=ItemSlot.ACCESSORY2
        )

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.ACCESSORY2.value] == accessory_id
        assert char.equipped_slots[ItemSlot.ACCESSORY1.value] is None

    def test_equip_accessory_to_wrong_slot_type_fails(self, state_char, accessory_id):
        char = _get_char(state_char)
        _add_item(char, accessory_id)

        result = equip_item(state_char, char.character_id, accessory_id, slot=ItemSlot.HEAD)

        assert not result.ok


# ---------------------------------------------------------------------------
# Unequipping
# ---------------------------------------------------------------------------

class TestUnequip:
    def test_unequip_weapon(self, state_char, weapon_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)
        equip_item(state_char, char.character_id, weapon_id)

        result = unequip_item(state_char, char.character_id, ItemSlot.MAIN_HAND)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.MAIN_HAND.value] is None
        inv = next(i for i in char.inventory if i.item_id == weapon_id)
        assert inv.equipped is False

    def test_unequip_head_gear(self, state_char, head_gear_id):
        char = _get_char(state_char)
        _add_item(char, head_gear_id)
        equip_item(state_char, char.character_id, head_gear_id)

        result = unequip_item(state_char, char.character_id, ItemSlot.HEAD)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.HEAD.value] is None

    def test_unequip_accessory1(self, state_char, accessory_id):
        char = _get_char(state_char)
        _add_item(char, accessory_id)
        equip_item(state_char, char.character_id, accessory_id)

        result = unequip_item(state_char, char.character_id, ItemSlot.ACCESSORY1)

        assert result.ok, result.error
        assert char.equipped_slots[ItemSlot.ACCESSORY1.value] is None

    def test_unequip_empty_slot_fails(self, state_char):
        char = _get_char(state_char)

        result = unequip_item(state_char, char.character_id, ItemSlot.MAIN_HAND)

        assert not result.ok


# ---------------------------------------------------------------------------
# Stat derivation (defense / resistance)
# ---------------------------------------------------------------------------

class TestStatDerivation:
    def test_defense_from_equipped_gear(self, state_char):
        """Equipping Gear with defense > 0 raises character.defense."""
        # Find any Gear with defense > 0
        gear_id = next(
            (item_id for item_id, defn in ITEM_REGISTRY.items()
             if isinstance(defn, Gear) and defn.defense > 0),
            None,
        )
        if gear_id is None:
            pytest.skip("No Gear with defense > 0 in ITEM_REGISTRY")

        char = _get_char(state_char)
        _add_item(char, gear_id)
        assert char.defense == 0

        equip_item(state_char, char.character_id, gear_id)

        expected = ITEM_REGISTRY[gear_id].defense
        assert char.defense == expected

    def test_resistance_from_equipped_gear(self, state_char):
        gear_id = next(
            (item_id for item_id, defn in ITEM_REGISTRY.items()
             if isinstance(defn, Gear) and defn.resistance > 0),
            None,
        )
        if gear_id is None:
            pytest.skip("No Gear with resistance > 0 in ITEM_REGISTRY")

        char = _get_char(state_char)
        _add_item(char, gear_id)
        assert char.resistance == 0

        equip_item(state_char, char.character_id, gear_id)

        expected = ITEM_REGISTRY[gear_id].resistance
        assert char.resistance == expected

    def test_defense_zero_after_unequip(self, state_char):
        gear_id = next(
            (item_id for item_id, defn in ITEM_REGISTRY.items()
             if isinstance(defn, Gear) and defn.defense > 0),
            None,
        )
        if gear_id is None:
            pytest.skip("No Gear with defense > 0 in ITEM_REGISTRY")

        char = _get_char(state_char)
        _add_item(char, gear_id)

        equip_item(state_char, char.character_id, gear_id)
        assert char.defense > 0

        # Find which slot it went into
        slot = next(
            s for s in ItemSlot
            if char.equipped_slots.get(s.value) == gear_id
        )
        unequip_item(state_char, char.character_id, slot)
        assert char.defense == 0

    def test_defense_stacks_across_slots(self, state_char):
        """Multiple pieces of gear in different slots all contribute to defense."""
        gear_with_defense = [
            (item_id, defn) for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Gear) and defn.defense > 0
        ]
        # Need at least two pieces in *different* slot types
        seen_slots = {}
        for item_id, defn in gear_with_defense:
            seen_slots.setdefault(defn.slot, (item_id, defn))

        if len(seen_slots) < 2:
            pytest.skip("Need gear with defense > 0 in at least 2 different slots")

        slot_items = list(seen_slots.values())[:2]
        char = _get_char(state_char)
        expected_total = 0
        for item_id, defn in slot_items:
            _add_item(char, item_id)
            equip_item(state_char, char.character_id, item_id)
            expected_total += defn.defense

        assert char.defense == expected_total

    def test_unequipped_weapon_does_not_affect_defense(self, state_char, weapon_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)
        equip_item(state_char, char.character_id, weapon_id)
        # Weapons don't add defense
        assert char.defense == 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestEquipErrors:
    def test_equip_item_not_in_inventory(self, state_char, weapon_id):
        char = _get_char(state_char)
        # weapon_id is NOT in inventory

        result = equip_item(state_char, char.character_id, weapon_id)

        assert not result.ok

    def test_equip_unknown_item_id(self, state_char):
        char = _get_char(state_char)
        _add_item(char, "nonexistent_item_xyz")

        result = equip_item(state_char, char.character_id, "nonexistent_item_xyz")

        assert not result.ok

    def test_equip_non_equipable_item(self, state_char):
        """Plain Item (not EquipItem) should fail gracefully."""
        plain_id = next(
            (item_id for item_id, defn in ITEM_REGISTRY.items()
             if type(defn).__name__ == "Item"),
            None,
        )
        if plain_id is None:
            pytest.skip("No plain Item in ITEM_REGISTRY")

        char = _get_char(state_char)
        _add_item(char, plain_id)

        result = equip_item(state_char, char.character_id, plain_id)

        assert not result.ok

    def test_unequip_nonexistent_character(self, state_char):
        from uuid import uuid4
        result = unequip_item(state_char, uuid4(), ItemSlot.HEAD)
        assert not result.ok

    def test_equip_nonexistent_character(self, state_char, weapon_id):
        from uuid import uuid4
        result = equip_item(state_char, uuid4(), weapon_id)
        assert not result.ok


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestEquipSerialization:
    def test_equipped_slots_survive_roundtrip(self, state_char, weapon_id, head_gear_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)
        _add_item(char, head_gear_id)
        equip_item(state_char, char.character_id, weapon_id)
        equip_item(state_char, char.character_id, head_gear_id)

        restored = deserialize_state(serialize_state(state_char))

        rest_char = next(iter(restored.characters.values()))
        assert rest_char.equipped_slots[ItemSlot.MAIN_HAND.value] == weapon_id
        assert rest_char.equipped_slots[ItemSlot.HEAD.value] == head_gear_id

    def test_unequipped_slots_are_none_after_roundtrip(self, state_char):
        restored = deserialize_state(serialize_state(state_char))
        rest_char = next(iter(restored.characters.values()))
        for slot in UI_SLOTS:
            assert rest_char.equipped_slots[slot] is None

    def test_equipped_flag_survives_roundtrip(self, state_char, weapon_id):
        char = _get_char(state_char)
        _add_item(char, weapon_id)
        equip_item(state_char, char.character_id, weapon_id)

        restored = deserialize_state(serialize_state(state_char))
        rest_char = next(iter(restored.characters.values()))
        inv = next(i for i in rest_char.inventory if i.item_id == weapon_id)
        assert inv.equipped is True

    def test_old_saves_without_equipped_slots_load_cleanly(self, state_char):
        """Simulates loading a save that predates equipped_slots."""
        import json
        raw = json.loads(serialize_state(state_char))
        # Strip equipped_slots from each character as if it were an old save
        for cdata in raw["characters"].values():
            cdata.pop("equipped_slots", None)

        restored = deserialize_state(json.dumps(raw))
        rest_char = next(iter(restored.characters.values()))
        # All slots should default to None
        for slot in UI_SLOTS:
            assert rest_char.equipped_slots[slot] is None
