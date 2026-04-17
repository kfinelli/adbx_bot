"""
Light source management for the dungeon crawler engine.
"""

from engine.data_loader import ITEM_REGISTRY
from models import GameState


def _tick_light(state: GameState) -> None:
    """Decrement equipped light items by one turn. Called by resolve_turn (exploration only)."""
    if state.party is None:
        return
    for char_id in state.party.member_ids:
        char = state.characters.get(char_id)
        if char is None:
            continue
        for item_id in list(char.equipped_slots.values()):
            if item_id is None:
                continue
            defn = ITEM_REGISTRY.get(item_id)
            if defn is None or getattr(defn, "max_light_turns", None) is None:
                continue
            inv_item = next(
                (i for i in char.inventory if i.item_id == item_id and i.equipped),
                None,
            )
            if inv_item is None or inv_item.charges is None or inv_item.charges <= 0:
                continue
            inv_item.charges -= 1
            if inv_item.charges <= 0:
                _handle_light_burnout(state, char, inv_item, defn)


def _handle_light_burnout(state, char, inv_item, defn) -> None:
    fuel_id = getattr(defn, "fuel_item_id", None)
    if fuel_id:
        fuel = next(
            (i for i in char.inventory if i.item_id == fuel_id and not i.equipped),
            None,
        )
        if fuel:
            fuel.quantity -= 1
            if fuel.quantity <= 0:
                char.inventory.remove(fuel)
            inv_item.charges = defn.max_light_turns
        # else: no fuel → lantern stays at 0 charges (dark, still equipped)
    else:
        # Torch: remove from inventory and clear the slot
        if inv_item in char.inventory:
            char.inventory.remove(inv_item)
        for slot_name, sid in list(char.equipped_slots.items()):
            if sid == inv_item.item_id:
                char.equipped_slots[slot_name] = None
                break
