"""
Room management for the dungeon crawler engine.
"""

from typing import TYPE_CHECKING

from models import (
    DoorState,
    Dungeon,
    Exit,
    GameState,
    InventoryItem,
    Room,
    RoomItem,
    RoomItemVisibility,
)
from validation import (
    validate_description,
    validate_door_state,
    validate_non_empty_string,
)

from .helpers import _err, _now, _ok, _resolve_room
from .strings import fmt_string, get_string

if TYPE_CHECKING:
    from engine.item import Item


class RoomManager:
    """Manages rooms, features, and exits."""

    def register_room(self, state: GameState, room: Room):
        """
        Add a room to the dungeon graph without moving the party into it.
        Used by the web UI when authoring rooms before or during a session.
        To also move the party in, call move_party_to_room() afterwards.
        """
        if state.dungeon is None:
            state.dungeon = Dungeon(name="The Dungeon")

        # Validate room name
        if not room.name or not room.name.strip():
            return _err(state, get_string("room.errors.name_empty"))

        # Validate room description length
        if room.description and len(room.description) > 1000:
            return _err(state, get_string("room.errors.description_too_long"))

        # Validate room notes length
        if room.notes and len(room.notes) > 2000:
            return _err(state, get_string("room.errors.notes_too_long"))

        state.dungeon.rooms[room.room_id] = room
        state.updated_at = _now()
        return _ok(state, fmt_string("room.added", name=room.name))

    def set_room(self, state: GameState, room: Room):
        """
        DM creates a new room on the fly and immediately moves the party in.
        Adds it to the dungeon graph and sets it as current.
        Used by the /dm_setroom slash command (no room_id) path.
        For web UI room creation, use register_room() instead.
        """
        result = self.register_room(state, room)
        if not result.ok:
            return result

        state.current_room_id = room.room_id
        room.visited = True
        state.updated_at = _now()
        return _ok(state, fmt_string("room.entered", name=room.name))

    def move_party_to_room(self, state: GameState, room_id):
        """
        Move the party into an already-authored room in the dungeon graph.

        - Looks up the room by ID; fails if not found.
        - Marks the room visited.
        - Does NOT modify the room's features, exits, or any other authored data.
        - NPCs in the roster remain in their rooms; use npc_roster for persistent NPCs.
        """
        if state.dungeon is None:
            return _err(state, get_string("room.errors.no_dungeon"))
        room = state.dungeon.rooms.get(room_id)
        if room is None:
            return _err(state, fmt_string("room.errors.not_found", room_id=room_id))
        state.current_room_id = room_id
        room.visited = True
        state.updated_at = _now()
        return _ok(state, fmt_string("room.entered", name=room.name))

    def update_room(
        self,
        state:       GameState,
        room_id,
        name:        str,
        description: str,
        notes:       str = "",
    ):
        """Edit the name, description, and DM notes of an existing room."""
        if not name.strip():
            return _err(state, get_string("room.errors.name_empty"))
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, fmt_string("room.errors.not_found", room_id=room_id))
        room.name        = name.strip()
        room.description = description
        room.notes       = notes
        state.updated_at = _now()
        return _ok(state, fmt_string("room.updated", name=room.name))

    def delete_feature(
        self,
        state:      GameState,
        feature_id,
        room_id = None,
    ):
        """Delete a feature from a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        before = len(room.features)
        room.features = [f for f in room.features if f.feature_id != feature_id]
        if len(room.features) == before:
            return _err(state, fmt_string("room.feature.not_found", feature_id=feature_id))
        state.updated_at = _now()
        return _ok(state, get_string("room.feature.deleted"))

    def update_feature(
        self,
        state:       GameState,
        feature_id,
        name:        str,
        description: str,
        state_str:   str,
        notes:       str = "",
        room_id = None,
    ):
        """Update a feature in a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        feat = next((f for f in room.features if f.feature_id == feature_id), None)
        if feat is None:
            return _err(state, fmt_string("room.feature.not_found", feature_id=feature_id))
        if not name.strip():
            return _err(state, get_string("room.feature.errors.name_empty"))
        feat.name        = name.strip()
        feat.description = description
        feat.state       = state_str
        feat.notes       = notes
        state.updated_at = _now()
        return _ok(state, fmt_string("room.feature.updated", name=feat.name))

    def delete_exit(
        self,
        state:   GameState,
        exit_id,
        room_id = None,
    ):
        """Delete an exit from a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        before = len(room.exits)
        room.exits = [e for e in room.exits if e.exit_id != exit_id]
        if len(room.exits) == before:
            return _err(state, fmt_string("room.exit.not_found", exit_id=exit_id))
        state.updated_at = _now()
        return _ok(state, "Exit deleted.")

    def update_exit(
        self,
        state:          GameState,
        exit_id,
        label:          str,
        description:    str,
        door_state,
        destination_id = None,
        notes:          str = "",
        auto_move:      bool = False,
        hidden:         bool = False,
        room_id = None,
    ):
        """Update an exit in a room."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        ex = next((e for e in room.exits if e.exit_id == exit_id), None)
        if ex is None:
            return _err(state, fmt_string("room.exit.not_found", exit_id=exit_id))
        if not label.strip():
            return _err(state, get_string("room.exit.errors.label_empty"))
        ex.label          = label.strip()
        ex.description    = description
        ex.door_state     = door_state
        ex.destination_id = destination_id
        ex.notes          = notes
        ex.auto_move      = auto_move
        ex.hidden         = hidden
        state.updated_at  = _now()
        return _ok(state, fmt_string("room.exit.updated", label=ex.label))

    def set_feature_state(
        self,
        state:      GameState,
        feature_id,
        new_state:  str,
        room_id = None,
    ):
        """Update the state string of a room feature."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        # Validate new state
        state_result = validate_non_empty_string(new_state, "Feature state", max_length=100)
        if not state_result:
            return _err(state, state_result.error)

        for feat in room.features:
            if feat.feature_id == feature_id:
                feat.state = state_result.value
                state.updated_at = _now()
                return _ok(state, fmt_string("room.feature.state_set", name=feat.name, state=state_result.value))
        return _err(state, fmt_string("room.feature.not_found", feature_id=feature_id))

    def set_exit_state(
        self,
        state,
        exit_id,
        new_state,
        room_id = None,
    ):
        """Set the door state of an exit."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        # Validate door state
        state_result = validate_door_state(new_state)
        if not state_result:
            return _err(state, state_result.error)

        for ex in room.exits:
            if ex.exit_id == exit_id:
                ex.door_state = state_result.value
                state.updated_at = _now()
                return _ok(state, f"Exit '{ex.label}' → {state_result.value.value}.")
        return _err(state, f"Exit {exit_id} not found.")

    def set_exit_visibility(
        self,
        state,
        exit_id,
        hidden: bool,
        room_id=None,
    ):
        """Show or hide an exit from player views."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))
        for ex in room.exits:
            if ex.exit_id == exit_id:
                ex.hidden = hidden
                state.updated_at = _now()
                label = "hidden" if hidden else "visible"
                return _ok(state, f"Exit '{ex.label}' is now {label}.")
        return _err(state, f"Exit {exit_id} not found.")

    def add_exit(
        self,
        state:          GameState,
        label:          str,
        description:    str,
        door_state=DoorState.OPEN,
        notes:          str = "",
        room_id=None,
        destination_id=None,
    ):
        """DM adds a new exit."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        # Validate label
        label_result = validate_non_empty_string(label, "Exit label", max_length=50)
        if not label_result:
            return _err(state, label_result.error)

        # Validate description
        desc_result = validate_description(description, "Exit description", max_length=200)
        if not desc_result:
            return _err(state, desc_result.error)

        # Validate door state
        door_result = validate_door_state(door_state)
        if not door_result:
            return _err(state, door_result.error)

        # Validate notes
        notes_result = validate_description(notes, "Exit notes", max_length=500, allow_empty=True)
        if not notes_result:
            return _err(state, notes_result.error)

        exit_ = Exit(
            label=label_result.value,
            description=desc_result.value,
            door_state=door_result.value,
            notes=notes_result.value,
        )
        if destination_id is not None and state.dungeon and destination_id in state.dungeon.rooms:
            exit_.destination_id = destination_id
        room.exits.append(exit_)
        n = len(room.exits)
        state.updated_at = _now()
        return _ok(state, f"Exit {n} added: {label_result.value}.")

    @staticmethod
    def _build_room_item(
        defn: "Item",
        quantity: int,
        visibility: RoomItemVisibility,
        notes: str,
    ) -> RoomItem:
        # Caller (add_room_item) must have resolved defn from ITEM_REGISTRY.
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import ChargeWeapon, ContainerItem, UtilitySpell

        item = InventoryItem(item_id=defn.item_id, quantity=quantity)
        contained: list[InventoryItem] = []

        if isinstance(defn, (ChargeWeapon, UtilitySpell)):
            item.charges = defn.maxCharges
        elif isinstance(defn, ContainerItem):
            for spell_id in defn.contained_item_ids:
                spell_def = ITEM_REGISTRY.get(spell_id)
                spell_charges = (
                    spell_def.maxCharges
                    if isinstance(spell_def, (ChargeWeapon, UtilitySpell))
                    else None
                )
                contained.append(
                    InventoryItem(
                        item_id=spell_id,
                        quantity=1,
                        container_id=item.instance_id,
                        charges=spell_charges,
                    )
                )

        return RoomItem(item=item, contained=contained, visibility=visibility, notes=notes)

    def add_room_item(
        self,
        state: GameState,
        item_id: str,
        quantity: int = 1,
        visibility: RoomItemVisibility = RoomItemVisibility.ACCESSIBLE,
        notes: str = "",
        room_id = None,
    ):
        """DM adds an item to the room's floor inventory."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        from engine.data_loader import ITEM_REGISTRY
        defn = ITEM_REGISTRY.get(item_id)
        if defn is None:
            return _err(state, f"Unknown item '{item_id}'.")
        if quantity < 1:
            return _err(state, "Quantity must be at least 1.")

        ri = self._build_room_item(defn, quantity, visibility, notes)
        room.items.append(ri)
        state.updated_at = _now()
        return _ok(state, f"{defn.name} added to room.")

    def remove_room_item(self, state: GameState, instance_id: str, room_id = None):
        """Remove an item from the room's floor inventory."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        before = len(room.items)
        room.items = [ri for ri in room.items if ri.item.instance_id != instance_id]
        if len(room.items) == before:
            return _err(state, "Item not found.")

        state.updated_at = _now()
        return _ok(state, "Item removed from room.")

    def set_room_item_visibility(
        self,
        state: GameState,
        instance_id: str,
        visibility: RoomItemVisibility,
        room_id = None,
    ):
        """Change the visibility state of a room item."""
        room = _resolve_room(state, room_id)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        for ri in room.items:
            if ri.item.instance_id == instance_id:
                ri.visibility = visibility
                state.updated_at = _now()
                from engine.data_loader import ITEM_REGISTRY
                defn = ITEM_REGISTRY.get(ri.item.item_id)
                name = defn.name if defn is not None else ri.item.item_id
                return _ok(state, f"{name} is now {visibility.value}.")

        return _err(state, "Item not found.")

    def pick_up_item(
        self,
        state: GameState,
        character_id,
        instance_id: str,
        quantity: int | None = None,
    ):
        """
        Player picks up an accessible room item into their inventory.

        Stacking rules (matching give_item): plain stackables merge onto an
        existing unequipped entry; ChargeWeapons, UtilitySpells, and
        containers always stay separate entries so per-instance charge state
        and contents are preserved. A picked-up item carrying charges (e.g.
        a dropped lit torch) also stays separate. Partial pickup of a
        stacked item splits the stack. Enforces the inventory slot limit.
        """
        from engine.character import _exceeds_inventory_capacity
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import ChargeWeapon, ContainerItem, UtilitySpell

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        room = _resolve_room(state, None)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        ri = next((r for r in room.items if r.item.instance_id == instance_id), None)
        if ri is None or ri.visibility is RoomItemVisibility.HIDDEN:
            return _err(state, "That item is no longer here.")
        defn = ITEM_REGISTRY.get(ri.item.item_id)
        name = defn.name if defn is not None else ri.item.item_id
        if ri.visibility is RoomItemVisibility.INACCESSIBLE:
            return _err(state, f"{name} is out of reach.")

        take = ri.item.quantity if quantity is None else quantity
        if take < 1 or take > ri.item.quantity:
            return _err(state, f"Cannot pick up {take}x {name}: only {ri.item.quantity} available.")

        over_capacity = (
            _exceeds_inventory_capacity(char, defn, take)
            if defn is not None
            else char.slots_used + take > char.inventory_size
        )
        if over_capacity:
            return _err(
                state,
                f"{char.name}'s inventory is full "
                f"({char.slots_used}/{char.inventory_size} slots used).",
            )

        # Merge plain stackables onto an existing unequipped entry (matching
        # give_item); charge-bearing items, containers, and items carrying
        # charges always move as their own entry.
        mergeable = (
            defn is not None
            and not isinstance(defn, (ChargeWeapon, UtilitySpell, ContainerItem))
            and ri.item.charges is None
        )
        existing = (
            next(
                (i for i in char.inventory
                 if i.item_id == ri.item.item_id
                 and not i.equipped
                 and i.container_id is None),
                None,
            )
            if mergeable else None
        )

        if existing is not None:
            existing.quantity += take
            if take == ri.item.quantity:
                room.items.remove(ri)
            else:
                ri.item.quantity -= take
        elif take == ri.item.quantity:
            # Move the whole entry, including any contained items.
            room.items.remove(ri)
            char.inventory.append(ri.item)
            char.inventory.extend(ri.contained)
        else:
            ri.item.quantity -= take
            char.inventory.append(InventoryItem(
                item_id=ri.item.item_id,
                quantity=take,
                charges=ri.item.charges,
            ))

        state.updated_at = _now()
        qty_str = f"{take}x " if take > 1 else ""
        return _ok(state, f"{char.name} picked up {qty_str}{name}.")

    def drop_item(
        self,
        state: GameState,
        character_id,
        instance_id: str,
        quantity: int | None = None,
    ):
        """
        Player drops an inventory item onto the floor of the current room.

        The dropped item becomes an accessible RoomItem. Containers carry
        their contained items with them; partial drops split the stack.
        Equipped items must be unequipped first.
        """
        from engine.data_loader import ITEM_REGISTRY
        from engine.item import ContainerItem

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        room = _resolve_room(state, None)
        if room is None:
            return _err(state, get_string("room.errors.no_current"))

        inv = next((i for i in char.inventory if i.instance_id == instance_id), None)
        if inv is None:
            return _err(state, f"Item not in {char.name}'s inventory.")
        defn = ITEM_REGISTRY.get(inv.item_id)
        name = defn.name if defn is not None else inv.item_id
        if inv.container_id is not None:
            return _err(state, f"{name} is contained in another item and can't be dropped on its own.")
        if inv.equipped:
            return _err(state, f"{name} is equipped and must be unequipped first.")

        take = inv.quantity if quantity is None else quantity
        if take < 1 or take > inv.quantity:
            return _err(state, f"Cannot drop {take}x {name}: only {inv.quantity} available.")

        if take == inv.quantity:
            char.inventory.remove(inv)
            contained = []
            if isinstance(defn, ContainerItem):
                contained = [i for i in char.inventory if i.container_id == inv.instance_id]
                char.inventory = [i for i in char.inventory if i.container_id != inv.instance_id]
            room.items.append(RoomItem(item=inv, contained=contained))
        else:
            inv.quantity -= take
            room.items.append(RoomItem(item=InventoryItem(
                item_id=inv.item_id,
                quantity=take,
                charges=inv.charges,
            )))

        state.updated_at = _now()
        qty_str = f"{take}x " if take > 1 else ""
        return _ok(state, f"{char.name} dropped {qty_str}{name}.")
