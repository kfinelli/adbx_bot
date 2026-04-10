"""
tests/test_throwable_tag.py — [Throwable N] tag: thrown weapon variant.

Covers:
  - equipped_weapons() emits a synthetic throwable entry for tagged weapons
  - Synthetic variant has correct item_id, name, and range from the tag number
  - Base weapon definition is not mutated
  - Weapons with both Agile and Throwable tags emit all three entries
  - Throwing removes the weapon from inventory and clears the main_hand slot
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import create_character, start_session
from models import CharacterClass, GameState, Party

# ---------------------------------------------------------------------------
# Helpers (mirror test_agile_tag.py pattern)
# ---------------------------------------------------------------------------

def _make_state_with_char():
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


def _equip_to_main_hand(char, item_id):
    from engine.azure_constants import ItemSlot
    from models import InventoryItem
    char.inventory.append(InventoryItem(item_id=item_id, equipped=True))
    char.equipped_slots[ItemSlot.MAIN_HAND.value] = item_id


# ---------------------------------------------------------------------------
# equipped_weapons() — Throwable variants
# ---------------------------------------------------------------------------

class TestThrowableEquippedWeapons:
    def test_throwable_weapon_has_throwable_entry(self):
        """Equipping a Throwable weapon adds a throwable synthetic entry."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "hand_axe")
        ids = [inv.item_id for inv, _ in char.equipped_weapons()]
        assert "hand_axe__throwable" in ids

    def test_throwable_variant_range_from_tag(self):
        """Throwable range equals the number in the 'Throwable N' tag."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "hand_axe")   # "Throwable 1"
        throwable_entry = next(
            (d for inv, d in char.equipped_weapons() if inv.item_id == "hand_axe__throwable"),
            None,
        )
        assert throwable_entry is not None
        assert throwable_entry.range == 1

    def test_throwable_variant_decorated_name(self):
        """Synthetic throwable variant name has ' [Throwable]' suffix."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "hand_axe")
        throwable_def = next(
            d for inv, d in char.equipped_weapons() if inv.item_id == "hand_axe__throwable"
        )
        assert throwable_def.name == "Hand Axe [Throwable]"

    def test_throwable_base_range_unchanged(self):
        """The base weapon entry still has its original range."""
        from engine.data_loader import ITEM_REGISTRY
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "hand_axe")
        base_def = next(d for inv, d in char.equipped_weapons() if inv.item_id == "hand_axe")
        assert base_def.range == ITEM_REGISTRY["hand_axe"].range

    def test_throwable_registry_not_mutated(self):
        """Generating the throwable variant must not mutate the registry definition."""
        from engine.data_loader import ITEM_REGISTRY
        original_range = ITEM_REGISTRY["hand_axe"].range
        original_name  = ITEM_REGISTRY["hand_axe"].name
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "hand_axe")
        char.equipped_weapons()  # trigger generation
        assert ITEM_REGISTRY["hand_axe"].range == original_range
        assert ITEM_REGISTRY["hand_axe"].name  == original_name

    def test_javelin_throwable_range_two(self):
        """Javelin has 'Throwable 2'; synthetic variant range is 2."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "javelin")
        throwable_def = next(
            d for inv, d in char.equipped_weapons() if inv.item_id == "javelin__throwable"
        )
        assert throwable_def.range == 2

    def test_agile_and_throwable_weapon_three_entries(self):
        """hand_axe has both Agile and Throwable 1 — yields base, agile, and throwable."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "hand_axe")
        weapons = char.equipped_weapons()
        ids = [inv.item_id for inv, _ in weapons]
        assert "hand_axe"           in ids  # base (physique)
        assert "hand_axe__agile"    in ids  # finesse variant
        assert "hand_axe__throwable" in ids  # throwable variant
        assert len(ids) == 3

    def test_non_throwable_weapon_no_throwable_entry(self):
        """A weapon without the Throwable tag produces no throwable entry."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "battle_axe")
        ids = [inv.item_id for inv, _ in char.equipped_weapons()]
        assert not any("__throwable" in i for i in ids)

    def test_all_throwable_weapons_produce_throwable_entry(self):
        """Every Throwable-tagged weapon in the registry yields a __throwable entry."""
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import Weapon
        throwable_ids = [
            iid for iid, d in ITEM_REGISTRY.items()
            if isinstance(d, Weapon) and any(t.startswith("Throwable ") for t in d.getTags())
        ]
        assert throwable_ids, "No Throwable weapons found in registry"
        for item_id in throwable_ids:
            state, char = _make_state_with_char()
            _equip_to_main_hand(char, item_id)
            ids = [inv.item_id for inv, _ in char.equipped_weapons()]
            assert f"{item_id}__throwable" in ids, f"{item_id} missing throwable entry"


# ---------------------------------------------------------------------------
# Integration: throwing removes the weapon
# ---------------------------------------------------------------------------

class TestThrowableConsumption:
    def _setup_throw(self, item_id):
        """Return (state, char, npc_id) with item equipped and an NPC at ENGAGE."""
        from engine import add_npc, enter_rounds, open_turn
        from models import NPC, RangeBand

        state, char = _make_state_with_char()
        _equip_to_main_hand(char, item_id)

        npc = NPC(name="Target", hp_current=1000, hp_max=1000)
        npc_id = npc.npc_id
        add_npc(state, npc)
        enter_rounds(state)
        open_turn(state)

        bf = state.battlefield
        bf.combatants[char.character_id].range_band = RangeBand.ENGAGE
        bf.combatants[npc_id].range_band = RangeBand.ENGAGE
        return state, char, npc_id

    def test_throw_removes_item_from_inventory(self):
        """After a throwable attack the item is no longer in char.inventory."""
        from engine.combat import CombatAction, _hook_weapon_attack

        state, char, npc_id = self._setup_throw("hand_axe")
        assert any(i.item_id == "hand_axe" for i in char.inventory)

        action = CombatAction(action_id="attack", target_id=npc_id, weapon_id="hand_axe__throwable")
        _hook_weapon_attack(state, char.character_id, action, [], {})

        assert not any(i.item_id == "hand_axe" for i in char.inventory)

    def test_throw_clears_main_hand_slot(self):
        """After a throwable attack the main_hand slot is cleared."""
        from engine.combat import CombatAction, _hook_weapon_attack

        state, char, npc_id = self._setup_throw("hand_axe")
        assert char.equipped_slots.get("main_hand") == "hand_axe"

        action = CombatAction(action_id="attack", target_id=npc_id, weapon_id="hand_axe__throwable")
        _hook_weapon_attack(state, char.character_id, action, [], {})

        assert char.equipped_slots.get("main_hand") is None

    def test_non_throwable_attack_does_not_remove_item(self):
        """A normal (non-throwable) attack does not consume the weapon."""
        from engine.combat import CombatAction, _hook_weapon_attack

        state, char, npc_id = self._setup_throw("hand_axe")
        action = CombatAction(action_id="attack", target_id=npc_id, weapon_id="hand_axe")
        _hook_weapon_attack(state, char.character_id, action, [], {})

        assert any(i.item_id == "hand_axe" for i in char.inventory)
        assert char.equipped_slots.get("main_hand") == "hand_axe"
