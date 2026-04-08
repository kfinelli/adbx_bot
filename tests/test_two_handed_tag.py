"""
tests/test_two_handed_tag.py — Two-Handed and Unwieldy weapon tags.

Two-Handed:
  - Equipping a two-handed weapon auto-unequips anything in the off-hand.
  - The success message notes the cleared off-hand item.
  - Equipping a two-handed weapon when off-hand is empty succeeds without extra note.
  - Equipping to the off-hand while a two-handed weapon is in main-hand is blocked.

Unwieldy:
  - Equipping an Unwieldy weapon fails when Physique < 400.
  - Equipping an Unwieldy weapon succeeds when Physique >= 400.
  - Unwieldy weapons that also carry Two-Handed inherit slot enforcement.

Items used:
  - spear      (rank D, Two-Handed)   — within KNIGHT's weapon rank C
  - war_maul   (rank D, Two-Handed + Unwieldy) — test item added for this feature
  - buckler    (off_hand slot, no tags)
  - shortsword (main_hand slot, no relevant tags)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import create_character, start_session
from engine.azure_constants import ItemSlot
from engine.azure_engine import CharacterClass
from models import GameState, InventoryItem, Party


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


def _add_to_inventory(char, item_id):
    char.inventory.append(InventoryItem(item_id=item_id))


def _force_equip(char, item_id, slot: ItemSlot):
    """Bypass engine logic to force an item into a slot (for test setup)."""
    char.inventory.append(InventoryItem(item_id=item_id, equipped=True))
    char.equipped_slots[slot.value] = item_id


# ---------------------------------------------------------------------------
# Two-Handed: equipping to main-hand
# ---------------------------------------------------------------------------

class TestTwoHandedEquip:
    def test_equip_two_handed_clears_off_hand(self):
        """Equipping a two-handed weapon auto-unequips the off-hand item."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _force_equip(char, "buckler", ItemSlot.OFF_HAND)
        _add_to_inventory(char, "spear")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "spear")

        assert result.ok, result.error
        assert char.equipped_slots.get(ItemSlot.OFF_HAND.value) is None
        oh_inv = next((i for i in char.inventory if i.item_id == "buckler"), None)
        assert oh_inv is not None and not oh_inv.equipped

    def test_equip_two_handed_clears_off_hand_message(self):
        """Success message mentions the unequipped off-hand item."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _force_equip(char, "buckler", ItemSlot.OFF_HAND)
        _add_to_inventory(char, "spear")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "spear")

        assert "Two-Handed" in result.message
        assert "Buckler" in result.message

    def test_equip_two_handed_empty_off_hand_no_extra_message(self):
        """No extra note when off-hand is already empty."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _add_to_inventory(char, "spear")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "spear")

        assert result.ok, result.error
        assert "Two-Handed" not in result.message

    def test_equip_two_handed_main_hand_slot_set(self):
        """Two-handed weapon ends up in the main-hand slot."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _add_to_inventory(char, "spear")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "spear")

        assert result.ok, result.error
        assert char.equipped_slots.get(ItemSlot.MAIN_HAND.value) == "spear"


# ---------------------------------------------------------------------------
# Two-Handed: blocking off-hand equip
# ---------------------------------------------------------------------------

class TestTwoHandedOffHandBlock:
    def test_equip_off_hand_blocked_when_two_handed_in_main(self):
        """Cannot equip to off-hand while a two-handed weapon occupies main-hand."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _force_equip(char, "spear", ItemSlot.MAIN_HAND)
        _add_to_inventory(char, "buckler")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "buckler")

        assert not result.ok
        assert "two-handed" in result.error.lower()

    def test_equip_off_hand_blocked_message_names_weapon(self):
        """Error message names the two-handed weapon preventing the equip."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _force_equip(char, "spear", ItemSlot.MAIN_HAND)
        _add_to_inventory(char, "buckler")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "buckler")

        assert "Spear" in result.error

    def test_equip_off_hand_allowed_when_no_two_handed_in_main(self):
        """Off-hand equip succeeds when main-hand holds a normal (one-handed) weapon."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _force_equip(char, "shortsword", ItemSlot.MAIN_HAND)
        _add_to_inventory(char, "buckler")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "buckler")

        assert result.ok, result.error

    def test_equip_off_hand_allowed_when_main_hand_empty(self):
        """Off-hand equip succeeds when main-hand is empty."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _add_to_inventory(char, "buckler")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "buckler")

        assert result.ok, result.error


# ---------------------------------------------------------------------------
# Unwieldy: Physique requirement
# ---------------------------------------------------------------------------

class TestUnwieldyPhysiqueRequirement:
    def test_unwieldy_blocked_low_physique(self):
        """Equipping an Unwieldy weapon fails when Physique < 400."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        char.ability_scores.physique = 300
        _add_to_inventory(char, "war_maul")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "war_maul")

        assert not result.ok
        assert "Unwieldy" in result.error

    def test_unwieldy_blocked_message_includes_physique(self):
        """Error message reports the character's current Physique."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        char.ability_scores.physique = 300
        _add_to_inventory(char, "war_maul")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "war_maul")

        assert "300" in result.error

    def test_unwieldy_allowed_exact_threshold(self):
        """Equipping an Unwieldy weapon succeeds at exactly 400 Physique."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        char.ability_scores.physique = 400
        _add_to_inventory(char, "war_maul")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "war_maul")

        assert result.ok, result.error

    def test_unwieldy_allowed_high_physique(self):
        """Equipping an Unwieldy weapon succeeds when Physique > 400."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        char.ability_scores.physique = 600
        _add_to_inventory(char, "war_maul")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "war_maul")

        assert result.ok, result.error

    def test_unwieldy_two_handed_clears_off_hand(self):
        """War Maul (Unwieldy + Two-Handed) auto-unequips off-hand on equip."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        char.ability_scores.physique = 400
        _force_equip(char, "buckler", ItemSlot.OFF_HAND)
        _add_to_inventory(char, "war_maul")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "war_maul")

        assert result.ok, result.error
        assert char.equipped_slots.get(ItemSlot.OFF_HAND.value) is None

    def test_unwieldy_two_handed_blocks_off_hand(self):
        """War Maul in main-hand blocks off-hand equip."""
        from engine.character import CharacterManager

        state, char = _make_state_with_char()
        _force_equip(char, "war_maul", ItemSlot.MAIN_HAND)
        _add_to_inventory(char, "buckler")

        mgr = CharacterManager()
        result = mgr.equip_item(state, char.character_id, "buckler")

        assert not result.ok
        assert "two-handed" in result.error.lower()
