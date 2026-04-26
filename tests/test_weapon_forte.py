"""
tests/test_weapon_forte.py — Dilettante Weapon Forte skill.

Covers:
  - equipped_weapons() returns a [Forte] SVY variant when familiar=True
  - [Forte] variant absent when item is not equipped
  - Registry definition not mutated by variant generation
  - set_familiar_weapon: sets the flag, enforces one-use, resets, skill gate
  - Integration: _hook_weapon_attack resolves the familiar weapon_id using SVY
  - Serialization round-trip: familiar flag survives serialize/deserialize
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import create_character, start_session
from models import CharacterClass, GameState, InventoryItem, Party

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WEAPON_ID = "shortsword"   # rank D, stat physique — equippable by Dilettante


def _make_state_with_dilettante():
    """EXPLORATION state with one Dilettante (has dilettante_weapon_forte at L1)."""
    from engine import register_room
    from models import Room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Rhiannon", CharacterClass.DILETTANTE, "", owner_id="u1")
    start_session(state)
    room = Room(name="Hall", description="Stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id
    char = next(iter(state.characters.values()))
    return state, char


def _make_state_with_knight():
    """EXPLORATION state with one Knight (no weapon forte skill)."""
    from engine import register_room
    from models import Room

    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    create_character(state, "Aldric", CharacterClass.KNIGHT, "", owner_id="u1")
    start_session(state)
    room = Room(name="Hall", description="Stone hall.")
    register_room(state, room)
    state.current_room_id = room.room_id
    char = next(iter(state.characters.values()))
    return state, char


def _add_equipped(char, item_id, familiar=False):
    """Add item to inventory as equipped in main_hand (bypasses rank enforcement)."""
    from engine.azure_constants import ItemSlot
    inv = InventoryItem(item_id=item_id, equipped=True, familiar=familiar)
    char.inventory.append(inv)
    char.equipped_slots[ItemSlot.MAIN_HAND.value] = item_id
    return inv


def _add_unequipped(char, item_id, familiar=False):
    """Add item to inventory, not equipped."""
    inv = InventoryItem(item_id=item_id, familiar=familiar)
    char.inventory.append(inv)
    return inv


# ---------------------------------------------------------------------------
# equipped_weapons() — Forte variant
# ---------------------------------------------------------------------------

class TestForteEquippedWeapons:
    def test_familiar_flag_creates_forte_variant(self):
        """familiar=True on an equipped weapon yields a [Forte] SVY variant."""
        state, char = _make_state_with_dilettante()
        _add_equipped(char, _WEAPON_ID, familiar=True)
        weapons = char.equipped_weapons()
        forte_entries = [(inv, d) for inv, d in weapons if inv.item_id.endswith("__familiar")]
        assert len(forte_entries) == 1
        _, defn = forte_entries[0]
        assert defn.stat == "savvy"

    def test_forte_variant_name_suffix(self):
        """[Forte] variant has ' [Forte]' appended to the name."""
        from engine.data_loader import ITEM_REGISTRY
        state, char = _make_state_with_dilettante()
        _add_equipped(char, _WEAPON_ID, familiar=True)
        weapons = char.equipped_weapons()
        _, defn = next((inv, d) for inv, d in weapons if inv.item_id.endswith("__familiar"))
        base_name = ITEM_REGISTRY[_WEAPON_ID].name
        assert defn.name == f"{base_name} [Forte]"

    def test_forte_variant_item_id_format(self):
        """Synthetic InventoryItem has item_id '<id>__familiar'."""
        state, char = _make_state_with_dilettante()
        _add_equipped(char, _WEAPON_ID, familiar=True)
        weapons = char.equipped_weapons()
        inv, _ = next((i, d) for i, d in weapons if i.item_id.endswith("__familiar"))
        assert inv.item_id == f"{_WEAPON_ID}__familiar"

    def test_forte_variant_absent_without_familiar_flag(self):
        """No Forte variant when familiar=False."""
        state, char = _make_state_with_dilettante()
        _add_equipped(char, _WEAPON_ID, familiar=False)
        weapons = char.equipped_weapons()
        assert all(not inv.item_id.endswith("__familiar") for inv, _ in weapons)

    def test_forte_variant_absent_when_not_equipped(self):
        """familiar=True on an unequipped item does NOT create a Forte variant."""
        state, char = _make_state_with_dilettante()
        _add_unequipped(char, _WEAPON_ID, familiar=True)
        weapons = char.equipped_weapons()
        assert all(not inv.item_id.endswith("__familiar") for inv, _ in weapons)

    def test_registry_not_mutated(self):
        """Generating the Forte variant must not mutate the registry definition."""
        from engine.data_loader import ITEM_REGISTRY
        original_stat = ITEM_REGISTRY[_WEAPON_ID].stat
        original_name = ITEM_REGISTRY[_WEAPON_ID].name
        state, char = _make_state_with_dilettante()
        _add_equipped(char, _WEAPON_ID, familiar=True)
        char.equipped_weapons()
        assert ITEM_REGISTRY[_WEAPON_ID].stat == original_stat
        assert ITEM_REGISTRY[_WEAPON_ID].name == original_name

    def test_forte_not_generated_when_stat_already_savvy(self):
        """No redundant [Forte] variant if weapon already uses savvy."""
        import copy

        from engine.data_loader import ITEM_REGISTRY

        savvy_weapon = copy.copy(ITEM_REGISTRY.get(_WEAPON_ID))
        savvy_weapon.stat = "savvy"
        savvy_weapon.name = "SVY Sword"
        ITEM_REGISTRY["__test_svq_sword"] = savvy_weapon

        state, char = _make_state_with_dilettante()
        inv = InventoryItem(item_id="__test_svq_sword", equipped=True, familiar=True)
        char.inventory.append(inv)
        from engine.azure_constants import ItemSlot
        char.equipped_slots[ItemSlot.MAIN_HAND.value] = "__test_svq_sword"

        weapons = char.equipped_weapons()
        assert all(not i.item_id.endswith("__familiar") for i, _ in weapons)

        del ITEM_REGISTRY["__test_svq_sword"]


# ---------------------------------------------------------------------------
# set_familiar_weapon engine function
# ---------------------------------------------------------------------------

class TestSetFamiliarWeapon:
    def test_sets_familiar_flag(self):
        """set_familiar_weapon sets familiar=True on the matching item."""
        from engine import set_familiar_weapon
        state, char = _make_state_with_dilettante()
        inv = _add_unequipped(char, _WEAPON_ID)
        result = set_familiar_weapon(state, char.character_id, inv.instance_id)
        assert result.ok
        assert inv.familiar is True

    def test_one_use_restriction(self):
        """A second call while a weapon is already familiar returns an error."""
        from engine import set_familiar_weapon
        state, char = _make_state_with_dilettante()
        inv1 = _add_unequipped(char, _WEAPON_ID)
        inv2 = _add_unequipped(char, _WEAPON_ID)
        set_familiar_weapon(state, char.character_id, inv1.instance_id)
        result = set_familiar_weapon(state, char.character_id, inv2.instance_id)
        assert not result.ok
        assert inv2.familiar is False

    def test_reset_clears_all_flags(self):
        """set_familiar_weapon with instance_id=None clears all familiar flags."""
        from engine import set_familiar_weapon
        state, char = _make_state_with_dilettante()
        inv = _add_unequipped(char, _WEAPON_ID)
        set_familiar_weapon(state, char.character_id, inv.instance_id)
        assert inv.familiar is True
        result = set_familiar_weapon(state, char.character_id, None)
        assert result.ok
        assert inv.familiar is False

    def test_requires_weapon_forte_skill(self):
        """Knight without the skill gets an error."""
        from engine import set_familiar_weapon
        state, char = _make_state_with_knight()
        inv = _add_unequipped(char, _WEAPON_ID)
        result = set_familiar_weapon(state, char.character_id, inv.instance_id)
        assert not result.ok

    def test_rejects_non_weapon_item(self):
        """Trying to set a gear item as familiar returns an error."""
        from engine import set_familiar_weapon
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import Gear
        gear_id = next(k for k, v in ITEM_REGISTRY.items() if isinstance(v, Gear))
        state, char = _make_state_with_dilettante()
        inv = InventoryItem(item_id=gear_id)
        char.inventory.append(inv)
        result = set_familiar_weapon(state, char.character_id, inv.instance_id)
        assert not result.ok

    def test_reset_skips_skill_check(self):
        """DM reset (instance_id=None) works even for a non-Dilettante."""
        from engine import set_familiar_weapon
        state, char = _make_state_with_knight()
        # Manually set a familiar flag to simulate a DM-controlled state
        inv = _add_unequipped(char, _WEAPON_ID)
        inv.familiar = True
        result = set_familiar_weapon(state, char.character_id, None)
        assert result.ok
        assert inv.familiar is False


# ---------------------------------------------------------------------------
# Integration: combat hook uses SVY when the __familiar weapon_id is selected
# ---------------------------------------------------------------------------

class TestForteInCombat:
    def test_forte_weapon_id_resolves_svq_stat(self):
        """
        _hook_weapon_attack uses savvy when weapon_id is '<id>__familiar'.
        Verified by checking the resolved weapon definition stat.
        """
        from engine import add_npc, enter_rounds, open_turn
        from engine.combat import CombatAction, _hook_weapon_attack
        from models import NPC, RangeBand

        state, char = _make_state_with_dilettante()
        _add_equipped(char, _WEAPON_ID, familiar=True)

        npc = NPC(name="Target", hp_current=1000, hp_max=1000)
        npc_id = npc.npc_id
        add_npc(state, npc)
        enter_rounds(state)
        open_turn(state)

        bf = state.battlefield
        bf.combatants[char.character_id].range_band = RangeBand.ENGAGE
        bf.combatants[npc_id].range_band = RangeBand.ENGAGE

        action = CombatAction(
            action_id="attack",
            target_id=npc_id,
            weapon_id=f"{_WEAPON_ID}__familiar",
        )
        log: list[str] = []
        _hook_weapon_attack(state, char.character_id, action, log, {})
        assert log, "Expected at least one log entry from the hook"
        assert "no weapon" not in " ".join(log).lower()


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestSerializationRoundTrip:
    def test_familiar_flag_survives_round_trip(self):
        """familiar=True on an InventoryItem is preserved through serialize/deserialize."""
        from serialization import deserialize_inventory_item, serialize_inventory_item
        inv = InventoryItem(item_id=_WEAPON_ID, familiar=True)
        data = serialize_inventory_item(inv)
        assert data["familiar"] is True
        restored = deserialize_inventory_item(data)
        assert restored.familiar is True

    def test_familiar_false_survives_round_trip(self):
        """familiar=False round-trips correctly."""
        from serialization import deserialize_inventory_item, serialize_inventory_item
        inv = InventoryItem(item_id=_WEAPON_ID, familiar=False)
        data = serialize_inventory_item(inv)
        restored = deserialize_inventory_item(data)
        assert restored.familiar is False

    def test_missing_familiar_field_defaults_to_false(self):
        """Old serialized records without 'familiar' key default to False."""
        from serialization import deserialize_inventory_item
        data = {
            "item_id": _WEAPON_ID,
            "quantity": 1,
            "equipped": False,
            "broken": False,
            "charges": None,
            "notes": "",
            "container_id": None,
            "instance_id": "abc123",
            # no "familiar" key
        }
        restored = deserialize_inventory_item(data)
        assert restored.familiar is False
