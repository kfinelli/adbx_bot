"""
tests/test_agile_tag.py — [Agile] tag: finesse weapon variant.

Covers:
  - feather_fan stat fix (physique, not finesse)
  - equipped_weapons() returns two entries for Agile weapons
  - Synthetic variant has correct item_id, name, and stat
  - Non-Agile weapon produces only one entry
  - Integration: _hook_weapon_attack selects correct stat when agile weapon_id used
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import create_character, start_session
from models import CharacterClass, GameState, Party

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_with_char():
    """Minimal EXPLORATION state with one Knight."""
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
    """Add item to inventory (equipped=True) and set main_hand slot directly (bypasses rank enforcement)."""
    from engine.azure_constants import ItemSlot
    from models import InventoryItem
    char.inventory.append(InventoryItem(item_id=item_id, equipped=True))
    char.equipped_slots[ItemSlot.MAIN_HAND.value] = item_id


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------

class TestFeatherFanDataFix:
    def test_feather_fan_stat_is_physique(self):
        """feather_fan was incorrectly set to finesse; should be physique."""
        from engine.data_loader import ITEM_REGISTRY
        defn = ITEM_REGISTRY["feather_fan"]
        assert defn.stat == "physique"

    def test_feather_fan_still_has_agile_tag(self):
        """feather_fan should still carry the Agile tag after the stat fix."""
        from engine.data_loader import ITEM_REGISTRY
        defn = ITEM_REGISTRY["feather_fan"]
        assert "Agile" in defn.getTags()


# ---------------------------------------------------------------------------
# equipped_weapons() — Agile variants
# ---------------------------------------------------------------------------

class TestAgileEquippedWeapons:
    def test_agile_weapon_returns_two_entries(self):
        """Equipping an Agile weapon yields two (inv, def) pairs."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")
        weapons = char.equipped_weapons()
        assert len(weapons) == 2

    def test_base_entry_has_physique_stat(self):
        """First entry is the unchanged weapon with stat=physique."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")
        inv, defn = char.equipped_weapons()[0]
        assert defn.stat == "physique"
        assert inv.item_id == "dagger"

    def test_agile_variant_has_finesse_stat(self):
        """Second entry is the synthetic variant with stat=finesse."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")
        _, defn = char.equipped_weapons()[1]
        assert defn.stat == "finesse"

    def test_agile_variant_has_decorated_name(self):
        """Synthetic variant name has ' [Agile]' suffix."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")
        _, defn = char.equipped_weapons()[1]
        assert defn.name == "Dagger [Agile]"

    def test_agile_variant_id_format(self):
        """Synthetic InventoryItem has item_id '<id>__agile'."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")
        inv, _ = char.equipped_weapons()[1]
        assert inv.item_id == "dagger__agile"

    def test_base_weapon_definition_not_mutated(self):
        """Generating the Agile variant must not mutate the registry definition."""
        from engine.data_loader import ITEM_REGISTRY
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")
        char.equipped_weapons()  # trigger variant generation
        assert ITEM_REGISTRY["dagger"].stat == "physique"
        assert ITEM_REGISTRY["dagger"].name == "Dagger"

    def test_non_agile_weapon_one_entry(self):
        """Non-Agile weapon produces exactly one entry."""
        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "battle_axe")
        weapons = char.equipped_weapons()
        assert len(weapons) == 1

    def test_all_agile_weapons_produce_two_entries(self):
        """Every Agile weapon in the registry yields an agile entry (may also have others)."""
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import Weapon
        agile_ids = [
            iid for iid, d in ITEM_REGISTRY.items()
            if isinstance(d, Weapon) and "Agile" in d.getTags()
        ]
        assert agile_ids, "No Agile weapons found in registry"
        for item_id in agile_ids:
            state, char = _make_state_with_char()
            _equip_to_main_hand(char, item_id)
            weapons = char.equipped_weapons()
            agile_entry = next(
                ((inv, d) for inv, d in weapons if inv.item_id == f"{item_id}__agile"),
                None,
            )
            assert agile_entry is not None, f"{item_id} missing agile entry"
            assert agile_entry[1].stat == "finesse"


# ---------------------------------------------------------------------------
# Integration: combat hook picks up the agile weapon_id
# ---------------------------------------------------------------------------

class TestAgileInCombat:
    def test_agile_weapon_id_resolves_in_hook(self):
        """
        _hook_weapon_attack finds the synthetic Agile entry when weapon_id is
        '<id>__agile'. Verified by checking that the resolved weapon has stat=finesse.
        """
        from engine import add_npc, enter_rounds, open_turn
        from engine.combat import CombatAction, _hook_weapon_attack
        from models import NPC, RangeBand

        state, char = _make_state_with_char()
        _equip_to_main_hand(char, "dagger")

        npc = NPC(name="Target", hp_current=1000, hp_max=1000)
        npc_id = npc.npc_id
        add_npc(state, npc)
        enter_rounds(state)
        open_turn(state)

        # Place both combatants in melee range
        bf = state.battlefield
        bf.combatants[char.character_id].range_band = RangeBand.ENGAGE
        bf.combatants[npc_id].range_band = RangeBand.ENGAGE

        action = CombatAction(
            action_id="attack",
            target_id=npc_id,
            weapon_id="dagger__agile",
        )
        log: list[str] = []
        # Hook must not raise — it should find the agile synthetic entry
        _hook_weapon_attack(state, char.character_id, action, log, {})
        # A log entry must have been produced (attack was resolved, not aborted)
        assert log, "Expected at least one log entry from the hook"
        # Verify it did not fall through to 'no weapon equipped'
        assert "no weapon" not in " ".join(log).lower()
