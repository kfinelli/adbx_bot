"""
test_inventory.py — Tests for inventory slot limits and give_item().

Covers:
  - inventory_size formula: BASE_INVENTORY_SIZE + floor(PHY / POWER_LEVEL)
  - slots_used excludes equipped items, counts unequipped items by slot_cost * quantity
  - slots_used bundles light items: ceil(total_light_qty / BUNDLE_SIZE)
  - give_item() adds items and stacks non-charged duplicates
  - give_item() rejects when inventory is full
  - give_item() never stacks ChargeWeapons
  - give_item() light item bundle capacity enforcement
  - slot_cost defaults to 1 on all item definitions
"""

import pytest

from engine import create_character, equip_item, give_item
from engine.azure_constants import BASE_INVENTORY_SIZE, BUNDLE_SIZE
from engine.data_loader import ITEM_REGISTRY
from engine.item import ChargeWeapon, Gear, Item, Weapon
from models import AzureStats, CharacterClass, InventoryItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_char(state):
    return next(iter(state.characters.values()))


def _find_weapon_id():
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, Weapon) and not isinstance(defn, ChargeWeapon):
            return item_id
    return None


def _find_charge_weapon_id():
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, ChargeWeapon):
            return item_id
    return None


def _find_gear_id(slot: str):
    for item_id, defn in ITEM_REGISTRY.items():
        if isinstance(defn, Gear) and defn.slot == slot:
            return item_id
    return None


@pytest.fixture
def light_items(monkeypatch):
    """Register two light items temporarily for bundle tests."""
    inkwell = Item("test_inkwell", "Inkwell", isLight=True)
    tinderbox = Item("test_tinderbox", "Tinderbox", isLight=True)
    monkeypatch.setitem(ITEM_REGISTRY, "test_inkwell", inkwell)
    monkeypatch.setitem(ITEM_REGISTRY, "test_tinderbox", tinderbox)
    return "test_inkwell", "test_tinderbox"


# ---------------------------------------------------------------------------
# inventory_size property
# ---------------------------------------------------------------------------

class TestInventorySize:
    def test_base_size_zero_physique(self, bare_state):
        create_character(
            bare_state, name="Zero", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        assert char.inventory_size == BASE_INVENTORY_SIZE

    def test_size_adds_physique_bonus(self, bare_state):
        # PHY stored as 500 → floor(500/100) = 5 → size = BASE + 5
        create_character(
            bare_state, name="Strong", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=500, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        assert char.inventory_size == BASE_INVENTORY_SIZE + 5

    def test_size_truncates_partial_physique(self, bare_state):
        # PHY=150 → floor(150/100) = 1
        create_character(
            bare_state, name="Sturdy", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=150, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        assert char.inventory_size == BASE_INVENTORY_SIZE + 1

    def test_size_only_physique_counts(self, bare_state):
        # High FNS/RSN/SVY should not affect inventory_size
        create_character(
            bare_state, name="Clever", character_class=CharacterClass.MAGE,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=900, reason=900, savvy=900),
        )
        char = _get_char(bare_state)
        assert char.inventory_size == BASE_INVENTORY_SIZE


# ---------------------------------------------------------------------------
# slots_used property
# ---------------------------------------------------------------------------

class TestSlotsUsed:
    def test_empty_inventory_uses_zero_slots(self, bare_state):
        create_character(
            bare_state, name="Empty", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        assert char.slots_used == 0

    def test_unequipped_item_uses_one_slot(self, bare_state):
        wid = _find_weapon_id()
        assert wid is not None
        create_character(
            bare_state, name="Armed", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=wid))
        assert char.slots_used == 1

    def test_equipped_item_does_not_count(self, bare_state):
        wid = _find_weapon_id()
        assert wid is not None
        create_character(
            bare_state, name="Equipped", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=wid))
        equip_item(bare_state, char.character_id, wid)

        assert char.inventory[0].equipped is True
        assert char.slots_used == 0

    def test_mixed_equipped_and_unequipped(self, bare_state):
        wid = _find_weapon_id()
        gid = _find_gear_id("head")
        assert wid and gid
        create_character(
            bare_state, name="Mixed", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=wid))
        char.inventory.append(InventoryItem(item_id=gid))
        equip_item(bare_state, char.character_id, wid)

        # weapon equipped (0 slots), gear unequipped (1 slot)
        assert char.slots_used == 1

    def test_multiple_unequipped_items(self, bare_state):
        wid = _find_weapon_id()
        gid = _find_gear_id("head")
        assert wid and gid
        create_character(
            bare_state, name="Laden", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=wid))
        char.inventory.append(InventoryItem(item_id=gid))

        assert char.slots_used == 2


# ---------------------------------------------------------------------------
# give_item()
# ---------------------------------------------------------------------------

class TestGiveItem:
    def test_give_item_adds_to_inventory(self, bare_state):
        wid = _find_weapon_id()
        assert wid is not None
        create_character(
            bare_state, name="Receiver", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        result = give_item(bare_state, char.character_id, wid)

        assert result.ok, result.error
        assert any(i.item_id == wid for i in char.inventory)

    def test_give_item_returns_error_for_unknown_item(self, bare_state):
        create_character(
            bare_state, name="Test", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        result = give_item(bare_state, char.character_id, "nonexistent_xyz")

        assert not result.ok
        assert "Unknown item" in result.error

    def test_give_item_blocked_when_full(self, bare_state):
        wid = _find_weapon_id()
        gid = _find_gear_id("head")
        assert wid and gid
        create_character(
            bare_state, name="Full", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        # Fill inventory to capacity (BASE_INVENTORY_SIZE slots, PHY=0)
        capacity = char.inventory_size
        for _ in range(capacity):
            char.inventory.append(InventoryItem(item_id=wid))

        assert char.slots_used == capacity
        result = give_item(bare_state, char.character_id, gid)

        assert not result.ok
        assert "full" in result.error.lower()

    def test_give_item_allowed_when_slot_freed_by_equip(self, bare_state):
        wid = _find_weapon_id()
        gid = _find_gear_id("head")
        assert wid and gid
        create_character(
            bare_state, name="Smart", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        capacity = char.inventory_size
        # Fill to capacity then equip one — frees 1 slot
        for _ in range(capacity):
            char.inventory.append(InventoryItem(item_id=wid))
        equip_item(bare_state, char.character_id, wid)

        result = give_item(bare_state, char.character_id, gid)
        assert result.ok, result.error

    def test_give_item_stacks_non_charged_duplicates(self, bare_state):
        wid = _find_weapon_id()
        assert wid is not None
        create_character(
            bare_state, name="Stacker", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        give_item(bare_state, char.character_id, wid)
        give_item(bare_state, char.character_id, wid)

        entries = [i for i in char.inventory if i.item_id == wid]
        assert len(entries) == 1
        assert entries[0].quantity == 2

    def test_give_item_does_not_stack_charge_weapons(self, bare_state):
        cwid = _find_charge_weapon_id()
        if cwid is None:
            pytest.skip("No ChargeWeapon in ITEM_REGISTRY")
        create_character(
            bare_state, name="Mage", character_class=CharacterClass.MAGE,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        give_item(bare_state, char.character_id, cwid)
        give_item(bare_state, char.character_id, cwid)

        entries = [i for i in char.inventory if i.item_id == cwid]
        assert len(entries) == 2

    def test_give_charge_weapon_sets_charges(self, bare_state):
        cwid = _find_charge_weapon_id()
        if cwid is None:
            pytest.skip("No ChargeWeapon in ITEM_REGISTRY")
        create_character(
            bare_state, name="Mage", character_class=CharacterClass.MAGE,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        give_item(bare_state, char.character_id, cwid)

        entry = next(i for i in char.inventory if i.item_id == cwid)
        defn = ITEM_REGISTRY[cwid]
        assert entry.charges == defn.maxCharges

    def test_give_item_stacking_blocked_when_full(self, bare_state):
        """Stacking onto an existing entry is blocked when inventory is full (each unit costs a slot)."""
        create_character(
            bare_state, name="Full", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        capacity = char.inventory_size

        # Fill with distinct non-charged items (one per slot) up to capacity.
        distinct_ids = [
            item_id for item_id, defn in ITEM_REGISTRY.items()
            if not isinstance(defn, ChargeWeapon)
        ]
        assert len(distinct_ids) >= capacity, "Not enough distinct items in registry to fill inventory"
        fill_ids = distinct_ids[:capacity]
        for item_id in fill_ids:
            result = give_item(bare_state, char.character_id, item_id)
            assert result.ok, result.error

        assert char.slots_used == capacity
        # Adding another copy of an existing item still requires a free slot.
        result = give_item(bare_state, char.character_id, fill_ids[0])
        assert not result.ok
        assert "full" in result.error.lower()

    def test_give_item_unknown_character(self, bare_state):
        from uuid import uuid4
        wid = _find_weapon_id()
        result = give_item(bare_state, uuid4(), wid)
        assert not result.ok


# ---------------------------------------------------------------------------
# slot_cost on item definitions
# ---------------------------------------------------------------------------

class TestSlotCost:
    def test_all_registry_items_have_slot_cost(self):
        for item_id, defn in ITEM_REGISTRY.items():
            assert hasattr(defn, "slot_cost"), (
                f"Item '{item_id}' is missing slot_cost attribute"
            )

    def test_default_slot_cost_is_one(self):
        for item_id, defn in ITEM_REGISTRY.items():
            assert defn.slot_cost == 1, (
                f"Item '{item_id}' has unexpected slot_cost={defn.slot_cost}"
            )


# ---------------------------------------------------------------------------
# slots_used quantity multiplier (non-light items)
# ---------------------------------------------------------------------------

class TestSlotsUsedQuantity:
    def test_quantity_multiplies_slot_cost(self, bare_state):
        """3 unequipped copies of the same item = 3 slots."""
        wid = _find_weapon_id()
        assert wid is not None
        create_character(
            bare_state, name="Laden", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=wid, quantity=3))
        assert char.slots_used == 3

    def test_two_distinct_items_each_cost_one(self, bare_state):
        wid = _find_weapon_id()
        gid = _find_gear_id("head")
        assert wid and gid
        create_character(
            bare_state, name="Laden2", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=wid, quantity=1))
        char.inventory.append(InventoryItem(item_id=gid, quantity=1))
        assert char.slots_used == 2


# ---------------------------------------------------------------------------
# Light item bundling
# ---------------------------------------------------------------------------

class TestLightBundling:
    def test_one_light_item_costs_one_slot(self, bare_state, light_items):
        inkwell_id, _ = light_items
        create_character(
            bare_state, name="Carrier", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=inkwell_id, quantity=1))
        assert char.slots_used == 1

    def test_ten_light_items_cost_one_slot(self, bare_state, light_items):
        inkwell_id, _ = light_items
        create_character(
            bare_state, name="Carrier", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=inkwell_id, quantity=BUNDLE_SIZE))
        assert char.slots_used == 1

    def test_eleven_light_items_cost_two_slots(self, bare_state, light_items):
        inkwell_id, _ = light_items
        create_character(
            bare_state, name="Carrier", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=inkwell_id, quantity=BUNDLE_SIZE + 1))
        assert char.slots_used == 2

    def test_mixed_light_types_bundle_together(self, bare_state, light_items):
        """7 inkwells + 4 tinderboxes = 11 light items = 2 slots."""
        inkwell_id, tinderbox_id = light_items
        create_character(
            bare_state, name="Carrier", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
        )
        char = _get_char(bare_state)
        char.inventory.append(InventoryItem(item_id=inkwell_id, quantity=7))
        char.inventory.append(InventoryItem(item_id=tinderbox_id, quantity=4))
        assert char.slots_used == 2

    def test_give_light_item_fills_bundle_without_new_slot(self, bare_state, light_items):
        """Adding a light item that stays within the current bundle uses no new slot."""
        inkwell_id, tinderbox_id = light_items
        create_character(
            bare_state, name="Carrier", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        # Fill non-light slots to leave exactly 1 slot free.
        wid = _find_weapon_id()
        for _ in range(char.inventory_size - 1):
            char.inventory.append(InventoryItem(item_id=wid))
        # Add 9 inkwells (1 slot) — only 1 non-light slot free, total = capacity.
        char.inventory.append(InventoryItem(item_id=inkwell_id, quantity=9))
        assert char.slots_used == char.inventory_size

        # Adding the 10th inkwell stays within the same bundle slot.
        result = give_item(bare_state, char.character_id, inkwell_id)
        assert result.ok, result.error
        assert char.slots_used == char.inventory_size

    def test_give_light_item_blocked_when_new_bundle_slot_needed(self, bare_state, light_items):
        """Adding a light item that overflows to a new bundle slot is blocked when full."""
        inkwell_id, _ = light_items
        create_character(
            bare_state, name="Carrier", character_class=CharacterClass.KNIGHT,
            equipment_package="", owner_id="u1",
            ability_scores=AzureStats(physique=0, finesse=0, reason=0, savvy=0),
        )
        char = _get_char(bare_state)
        # Fill non-light slots to leave exactly 1 slot free.
        wid = _find_weapon_id()
        for _ in range(char.inventory_size - 1):
            char.inventory.append(InventoryItem(item_id=wid))
        # Fill the remaining slot with a full bundle of 10 inkwells.
        char.inventory.append(InventoryItem(item_id=inkwell_id, quantity=BUNDLE_SIZE))
        assert char.slots_used == char.inventory_size

        # The 11th inkwell would need a 2nd bundle slot — no room.
        result = give_item(bare_state, char.character_id, inkwell_id)
        assert not result.ok
        assert "full" in result.error.lower()
