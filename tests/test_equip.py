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

from engine import create_character, equip_item, give_item, remove_item, unequip_item
from engine.azure_constants import UI_SLOTS, ItemSlot
from engine.data_loader import ITEM_REGISTRY
from engine.item import ChargeWeapon, ContainerItem, Gear, UtilitySpell, Weapon
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
    """Return the first physical Weapon (non-arcane rank) so tests work with a Knight."""
    from engine.item import ChargeWeapon
    _ARCANE = {"V", "W", "X", "Y", "Z"}
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon) and defn.rank not in _ARCANE:
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
        from engine.item import ChargeWeapon
        _ARCANE = {"V", "W", "X", "Y", "Z"}
        w1 = _find_weapon_id()
        # Find a *different* physical weapon (one the Knight test char can equip)
        w2 = None
        for item_id, defn in ITEM_REGISTRY.items():
            if (isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
                    and defn.rank not in _ARCANE and item_id != w1):
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
        # Only look for physical-ranked gear so the Knight test character can equip it.
        # Arcane resistance gear (robes, etc.) requires spell_rank which Knight lacks.
        _PHYSICAL = {"E", "D", "C", "B", "A"}
        gear_id = next(
            (item_id for item_id, defn in ITEM_REGISTRY.items()
             if isinstance(defn, Gear) and defn.resistance > 0 and defn.rank in _PHYSICAL),
            None,
        )
        if gear_id is None:
            pytest.skip("No physical-ranked Gear with resistance > 0 in ITEM_REGISTRY")

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

    def test_equip_displacement_blocked_when_inventory_full(self, state_char):
        """Equipping a new item must not push inventory over the limit by displacing the old one.

        The overflow can only occur when the displaced item costs more inventory slots than
        the new item. We patch one weapon's slot_cost to 2 to manufacture that condition.
        """
        from engine.item import ChargeWeapon
        _ARCANE = {"V", "W", "X", "Y", "Z"}
        # Find two different physical weapons the Knight can equip
        weapons = [
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
            and defn.rank not in _ARCANE
        ]
        if len(weapons) < 2:
            pytest.skip("Need at least 2 physical weapons to test displacement")
        w1, w2 = weapons[0], weapons[1]

        char = _get_char(state_char)
        # Temporarily give w1 a slot_cost of 2 so displacing it would overflow inventory
        w1_defn = ITEM_REGISTRY[w1]
        original_cost = w1_defn.slot_cost
        w1_defn.slot_cost = 2
        try:
            # Equip w1 (cost=2, equipped so doesn't count toward slots_used)
            _add_item(char, w1)
            equip_item(state_char, char.character_id, w1)

            # Fill inventory to inventory_size - 1 slots with filler
            filler_id = next(
                item_id for item_id, defn in ITEM_REGISTRY.items()
                if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
                and defn.rank not in _ARCANE and item_id not in (w1, w2)
            )
            while char.slots_used < char.inventory_size - 1:
                _add_item(char, filler_id)

            # Add w2 (cost=1) — this succeeds because one slot is free
            give_result = give_item(state_char, char.character_id, w2)
            assert give_result.ok, "Setup failed: could not add w2 to inventory"
            assert char.slots_used == char.inventory_size, "Expected inventory to be full after giving w2"

            # Equip w2 — would displace w1 (cost=2) back to a full inventory: +2-1 = overflow
            result = equip_item(state_char, char.character_id, w2)

            assert not result.ok
            assert char.slots_used == char.inventory_size, "Inventory limit must not be exceeded"
        finally:
            w1_defn.slot_cost = original_cost

    def test_equip_displacement_blocked_when_light_stack_crosses_bundle_boundary(self, state_char):
        """Equipping a weapon must be blocked when the displaced light-item stack would push
        the light pool across a BUNDLE_SIZE boundary, costing an extra inventory slot.

        Scenario: a torch stack (qty = BUNDLE_SIZE + 1) is equipped. Inventory is full of
        non-light items. Equipping a new weapon would displace the stack back to inventory,
        which now requires 2 light slots instead of 0 (net +1 after the new item frees one
        non-light slot) — overflow by 1.
        """
        from engine.azure_constants import BUNDLE_SIZE
        from engine.item import ChargeWeapon

        _ARCANE = {"V", "W", "X", "Y", "Z"}
        torch_id = "torch"
        weapon_id = next(
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
            and defn.rank not in _ARCANE and item_id != torch_id
        )

        char = _get_char(state_char)

        # Build a torch stack of BUNDLE_SIZE + 1 (e.g. 11) directly in inventory
        stack_qty = BUNDLE_SIZE + 1
        char.inventory.append(InventoryItem(item_id=torch_id, quantity=stack_qty))
        # Equip the whole stack — frees stack_qty // BUNDLE_SIZE + 1 light slots
        equip_item(state_char, char.character_id, torch_id)
        assert any(i.item_id == torch_id and i.equipped for i in char.inventory), "torch should be equipped"

        # Fill every remaining inventory slot with non-light weapons
        while char.slots_used < char.inventory_size:
            char.inventory.append(InventoryItem(item_id=weapon_id, quantity=1))
        assert char.slots_used == char.inventory_size

        # Add the new weapon (one free slot needed — make room by temporarily
        # adjusting expected size, or just bypass give_item for setup)
        # Actually: with slots_used == inventory_size, give_item would fail.
        # Directly append to simulate a DM-granted item (the check being tested
        # is equip_item, not give_item).
        char.inventory.append(InventoryItem(item_id=weapon_id, quantity=1))
        # slots_used is now inventory_size + 1 from the raw append, but that's
        # intentional setup — we're testing that equip_item blocks the swap.
        # Reset: remove the last non-light filler so slots_used == inventory_size,
        # then add the new weapon properly.
        char.inventory.clear()
        char.inventory.append(InventoryItem(item_id=torch_id, quantity=stack_qty, equipped=True))
        # Fill non-light slots to inventory_size - 1 (leave one slot for the new weapon)
        while char.slots_used < char.inventory_size - 1:
            char.inventory.append(InventoryItem(item_id=weapon_id, quantity=1))
        # Give the weapon to buy (occupies the last slot)
        give_result = give_item(state_char, char.character_id, weapon_id)
        assert give_result.ok, f"Setup failed: {give_result.error}"
        assert char.slots_used == char.inventory_size
        char.equipped_slots["main_hand"] = torch_id  # restore equipped state

        # Equip the new weapon — displacing the torch stack (qty=BUNDLE_SIZE+1)
        # would require ceil((BUNDLE_SIZE+1)/BUNDLE_SIZE) = 2 light slots, but
        # only one non-light slot is freed: net overflow by 1.
        result = equip_item(state_char, char.character_id, weapon_id)

        assert not result.ok, f"Expected equip to be blocked but got: {result.message}"
        assert char.slots_used == char.inventory_size, "Inventory limit must not be exceeded"

    def test_unequip_light_item_blocked_when_bundle_boundary_crossed(self, state_char):
        """Unequipping a light item must be blocked when the light pool is at a BUNDLE_SIZE
        multiple and returning the item would cross to the next bundle slot.

        Scenario: inventory is full, unequipped light pool is exactly BUNDLE_SIZE (1 slot).
        Unequipping a dagger (light) would push pool to BUNDLE_SIZE+1 (2 slots) — overflow.
        """
        from engine.azure_constants import BUNDLE_SIZE
        from engine.item import ChargeWeapon

        _ARCANE = {"V", "W", "X", "Y", "Z"}
        dagger_id = "dagger"
        filler_id = next(
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
            and defn.rank not in _ARCANE and item_id != dagger_id
        )
        torch_id = "torch"

        char = _get_char(state_char)

        # Equip a dagger in MAIN_HAND (doesn't count toward slots_used)
        char.inventory.append(InventoryItem(item_id=dagger_id, quantity=1, equipped=True))
        char.equipped_slots["main_hand"] = dagger_id

        # Fill the light pool to exactly BUNDLE_SIZE (1 light slot used)
        char.inventory.append(InventoryItem(item_id=torch_id, quantity=BUNDLE_SIZE))

        # Fill the rest with non-light items until inventory is full
        while char.slots_used < char.inventory_size:
            char.inventory.append(InventoryItem(item_id=filler_id, quantity=1))

        assert char.slots_used == char.inventory_size, "Inventory must be full for this test"

        # Unequipping the dagger adds 1 to the light pool (BUNDLE_SIZE → BUNDLE_SIZE+1),
        # crossing the bundle boundary and requiring an extra slot — must be blocked.
        result = unequip_item(state_char, char.character_id, ItemSlot.MAIN_HAND)

        assert not result.ok, f"Expected unequip to be blocked but got: {result.message}"
        assert char.slots_used == char.inventory_size, "Inventory limit must not be exceeded"


# ---------------------------------------------------------------------------
# Stack-splitting on equip
# ---------------------------------------------------------------------------

class TestEquipStackSplit:
    def test_equip_splits_stack_of_non_light_items(self, state_char):
        """Equipping one item from a stack of N should leave N-1 unequipped and 1 equipped."""
        _ARCANE = {"V", "W", "X", "Y", "Z"}
        weapon_id = next(
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
            and defn.rank not in _ARCANE
        )
        char = _get_char(state_char)
        char.inventory.append(InventoryItem(item_id=weapon_id, quantity=3))

        equip_item(state_char, char.character_id, weapon_id)

        weapon_entries = [i for i in char.inventory if i.item_id == weapon_id]
        assert len(weapon_entries) == 2, "Stack should split into two entries"
        equipped = [i for i in weapon_entries if i.equipped]
        unequipped = [i for i in weapon_entries if not i.equipped]
        assert len(equipped) == 1 and equipped[0].quantity == 1
        assert len(unequipped) == 1 and unequipped[0].quantity == 2

    def test_equip_single_item_no_split(self, state_char):
        """Equipping a stack of 1 should not create a spurious empty entry."""
        _ARCANE = {"V", "W", "X", "Y", "Z"}
        weapon_id = next(
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon)
            and defn.rank not in _ARCANE
        )
        char = _get_char(state_char)
        char.inventory.append(InventoryItem(item_id=weapon_id, quantity=1))

        equip_item(state_char, char.character_id, weapon_id)

        weapon_entries = [i for i in char.inventory if i.item_id == weapon_id]
        assert len(weapon_entries) == 1, "No split should occur for a single-item stack"
        assert weapon_entries[0].equipped is True
        assert weapon_entries[0].quantity == 1


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


# ---------------------------------------------------------------------------
# Spellbook / ContainerItem charge tracking
# ---------------------------------------------------------------------------

@pytest.fixture
def state_mage(bare_state):
    """State with a single Mage character, no inventory."""
    create_character(
        bare_state,
        name="Vera",
        character_class=CharacterClass.MAGE,
        equipment_package="",
        owner_id="user_mage_test",
    )
    return bare_state


@pytest.fixture
def spellbook_id():
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, ContainerItem) and defn.contained_item_ids:
            return item_id
    return None


class TestSpellbookChargeTracking:
    def test_give_spellbook_adds_spell_items(self, state_mage, spellbook_id):
        assert spellbook_id is not None, "No ContainerItem found in ITEM_REGISTRY"
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, spellbook_id)

        book_inv = next(i for i in char.inventory if i.item_id == spellbook_id)
        book_def = ITEM_REGISTRY[spellbook_id]
        for spell_id in book_def.contained_item_ids:
            spell_inv = next(
                (i for i in char.inventory
                 if i.item_id == spell_id and i.container_id == book_inv.instance_id),
                None,
            )
            assert spell_inv is not None, f"Spell {spell_id} not added to inventory"
            spell_def = ITEM_REGISTRY.get(spell_id)
            if isinstance(spell_def, ChargeWeapon):
                assert spell_inv.charges == spell_def.maxCharges

    def test_remove_spellbook_removes_spell_items(self, state_mage, spellbook_id):
        assert spellbook_id is not None, "No ContainerItem found in ITEM_REGISTRY"
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, spellbook_id)

        book_inv = next(i for i in char.inventory if i.item_id == spellbook_id)
        book_instance_id = book_inv.instance_id
        book_def = ITEM_REGISTRY[spellbook_id]
        # Verify spells are present before removal
        for spell_id in book_def.contained_item_ids:
            assert any(
                i.item_id == spell_id and i.container_id == book_instance_id
                for i in char.inventory
            )

        remove_item(state_mage, char.character_id, spellbook_id)

        # Container and all its spell items should be gone
        assert not any(i.item_id == spellbook_id for i in char.inventory)
        for spell_id in book_def.contained_item_ids:
            assert not any(
                i.item_id == spell_id and i.container_id == book_instance_id
                for i in char.inventory
            )

    def test_give_two_spellbooks_no_duplicate_spells(self, state_mage, spellbook_id):
        assert spellbook_id is not None, "No ContainerItem found in ITEM_REGISTRY"
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, spellbook_id)
        give_item(state_mage, char.character_id, spellbook_id)

        book_def = ITEM_REGISTRY[spellbook_id]
        expected_spell_count = len(book_def.contained_item_ids) * 2
        actual_spell_count = sum(1 for i in char.inventory if i.container_id is not None)
        assert actual_spell_count == expected_spell_count, (
            f"Expected {expected_spell_count} contained spells, got {actual_spell_count}"
        )

        # Each book instance must have its own independent set of contained spells.
        books = [i for i in char.inventory if i.item_id == spellbook_id]
        assert len(books) == 2
        for book in books:
            children = [i for i in char.inventory if i.container_id == book.instance_id]
            assert len(children) == len(book_def.contained_item_ids), (
                f"Book {book.instance_id} has {len(children)} spells, "
                f"expected {len(book_def.contained_item_ids)}"
            )

    def test_remove_one_spellbook_leaves_other_intact(self, state_mage, spellbook_id):
        assert spellbook_id is not None, "No ContainerItem found in ITEM_REGISTRY"
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, spellbook_id)
        give_item(state_mage, char.character_id, spellbook_id)

        books = [i for i in char.inventory if i.item_id == spellbook_id]
        kept_instance_id = books[1].instance_id
        book_def = ITEM_REGISTRY[spellbook_id]

        remove_item(state_mage, char.character_id, spellbook_id)

        # One book and its spells remain
        assert sum(1 for i in char.inventory if i.item_id == spellbook_id) == 1
        remaining_spells = [i for i in char.inventory if i.container_id == kept_instance_id]
        assert len(remaining_spells) == len(book_def.contained_item_ids)


# ---------------------------------------------------------------------------
# UtilitySpell tests
# ---------------------------------------------------------------------------

@pytest.fixture
def utility_spell_id():
    """Return an item_id for a UtilitySpell in ITEM_REGISTRY, or None."""
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, UtilitySpell):
            return item_id
    return None


@pytest.fixture
def utility_spellbook_id():
    """Return a slot-less ContainerItem that contains utility spells."""
    for item_id, defn in ITEM_REGISTRY.items():
        if (
            isinstance(defn, ContainerItem)
            and defn.slot is None
            and any(
                isinstance(ITEM_REGISTRY.get(sid), UtilitySpell)
                for sid in defn.contained_item_ids
            )
        ):
            return item_id
    return None


class TestUtilitySpellLoading:
    def test_utility_spell_in_registry(self, utility_spell_id):
        assert utility_spell_id is not None, "No UtilitySpell found in ITEM_REGISTRY — check items.json"
        defn = ITEM_REGISTRY[utility_spell_id]
        assert isinstance(defn, UtilitySpell)
        assert defn.slot is None
        assert defn.description != "" or defn.otherAbilities != "" or defn.maxCharges is not None

    def test_utility_spell_fields_populated(self):
        """viviu_2 is a known finite-charge utility spell — verify its fields."""
        defn = ITEM_REGISTRY.get("viviu_2")
        assert defn is not None, "viviu_2 not in ITEM_REGISTRY"
        assert isinstance(defn, UtilitySpell)
        assert defn.maxCharges == 5
        assert defn.description == "Heals 100 damage"
        assert defn.rank == "V"

    def test_infinite_charge_utility_spell(self):
        """viviu_1 has max_charges -1 (infinite)."""
        defn = ITEM_REGISTRY.get("viviu_1")
        assert defn is not None
        assert defn.maxCharges == -1


class TestUtilitySpellbookGive:
    def test_give_utility_spellbook_adds_contained_spells(self, state_mage, utility_spellbook_id):
        assert utility_spellbook_id is not None, "No slot-less ContainerItem with utility spells found"
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, utility_spellbook_id)

        book_inv = next(i for i in char.inventory if i.item_id == utility_spellbook_id)
        book_def = ITEM_REGISTRY[utility_spellbook_id]
        for spell_id in book_def.contained_item_ids:
            spell_inv = next(
                (i for i in char.inventory
                 if i.item_id == spell_id and i.container_id == book_inv.instance_id),
                None,
            )
            assert spell_inv is not None, f"Spell {spell_id} not added to inventory"
            spell_def = ITEM_REGISTRY.get(spell_id)
            if isinstance(spell_def, UtilitySpell) and spell_def.maxCharges > 0:
                assert spell_inv.charges == spell_def.maxCharges

    def test_utility_spells_not_independently_equippable(self, state_mage, utility_spellbook_id):
        """Contained utility spells should have container_id set and no slot."""
        assert utility_spellbook_id is not None
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, utility_spellbook_id)

        book_inv = next(i for i in char.inventory if i.item_id == utility_spellbook_id)
        book_def = ITEM_REGISTRY[utility_spellbook_id]
        for spell_id in book_def.contained_item_ids:
            spell_inv = next(
                (i for i in char.inventory
                 if i.item_id == spell_id and i.container_id == book_inv.instance_id),
                None,
            )
            assert spell_inv is not None
            assert spell_inv.container_id == book_inv.instance_id
            assert not spell_inv.equipped


class TestUtilitySpellCharacterSheet:
    def test_character_sheet_shows_utility_spell_description(self, state_mage, utility_spellbook_id):
        """Inventory display lines for utility spells include name, charges, and description.

        Replicates the contained-item rendering from cogs/character_views._character_sheet
        without importing the cog (which pulls in discord).
        """
        assert utility_spellbook_id is not None
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, utility_spellbook_id)

        book_inv = next(i for i in char.inventory if i.item_id == utility_spellbook_id)

        # Build the same contained-item lines that _character_sheet produces.
        contained: dict[str, list] = {}
        for inv in char.inventory:
            if inv.container_id:
                contained.setdefault(inv.container_id, []).append(inv)

        lines = []
        for child in contained.get(book_inv.instance_id, []):
            cdefn = ITEM_REGISTRY.get(child.item_id)
            cname = cdefn.name if cdefn else child.item_id
            if child.charges is not None and cdefn is not None and hasattr(cdefn, "maxCharges"):
                charges = " (\u221e)" if cdefn.maxCharges < 0 else f" ({child.charges}/{cdefn.maxCharges})"
            else:
                charges = ""
            desc = f" \u2014 {cdefn.description}" if isinstance(cdefn, UtilitySpell) and cdefn.description else ""
            lines.append(f"    \u2514 {cname}{charges}{desc}")

        book_def = ITEM_REGISTRY[utility_spellbook_id]
        for spell_id in book_def.contained_item_ids:
            spell_def = ITEM_REGISTRY.get(spell_id)
            if not isinstance(spell_def, UtilitySpell):
                continue
            matching = [ln for ln in lines if spell_def.name in ln]
            assert matching, f"{spell_def.name} not found in rendered lines"
            if spell_def.description:
                assert any(spell_def.description in ln for ln in matching)


class TestUtilitySpellPersistence:
    def test_persistence_round_trip(self, state_mage, utility_spellbook_id):
        """Contained utility spell charges and container_id survive serialize/deserialize."""
        assert utility_spellbook_id is not None
        char = _get_char(state_mage)
        give_item(state_mage, char.character_id, utility_spellbook_id)

        restored = deserialize_state(serialize_state(state_mage))
        rest_char = next(iter(restored.characters.values()))

        book_inv = next(i for i in char.inventory if i.item_id == utility_spellbook_id)
        book_instance_id = book_inv.instance_id
        book_def = ITEM_REGISTRY[utility_spellbook_id]
        for spell_id in book_def.contained_item_ids:
            orig = next(
                (i for i in char.inventory
                 if i.item_id == spell_id and i.container_id == book_instance_id),
                None,
            )
            restored_inv = next(
                (i for i in rest_char.inventory
                 if i.item_id == spell_id and i.container_id == book_instance_id),
                None,
            )
            assert restored_inv is not None, f"Spell {spell_id} lost after round-trip"
            assert restored_inv.charges == orig.charges
            assert restored_inv.container_id == book_instance_id
