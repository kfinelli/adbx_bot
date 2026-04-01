"""
cogs/character_views.py — Character sheet renderer and equipment management views.

These are sent as DMs when a player clicks "My Character" or uses /character.
All views are transient (timeout > 0) — they are not registered as persistent.
"""

from __future__ import annotations

import discord

from engine import equip_item, unequip_item
from engine.azure_constants import UI_SLOTS, ItemSlot
from engine.data_loader import ITEM_REGISTRY
from engine.item import EquipItem
from store import save_session_async

# Human-readable labels for each slot.
_SLOT_LABELS: dict[ItemSlot, str] = {
    ItemSlot.MAIN_HAND:  "Main Hand",
    ItemSlot.OFF_HAND:   "Off Hand",
    ItemSlot.HEAD:       "Head",
    ItemSlot.BODY:       "Body",
    ItemSlot.ARMS:       "Arms",
    ItemSlot.LEGS:       "Legs",
    ItemSlot.ACCESSORY1: "Accessory 1",
    ItemSlot.ACCESSORY2: "Accessory 2",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


def _character_sheet(char, state) -> str:
    """Produce the character sheet code block string."""
    a = char.ability_scores
    leader_note = " (Party Leader)" if (
        state.party and state.party.leader_id == char.character_id
    ) else ""
    sep = "\u2500" * 32

    # Build a map of container_id → contained InventoryItems for nested display.
    _contained: dict[str, list] = {}
    for _i in char.inventory:
        if _i.container_id:
            _contained.setdefault(_i.container_id, []).append(_i)

    _inv_parts = []
    for i in char.inventory:
        if i.container_id:
            continue  # rendered under its container below
        line = f"  {i.quantity}x {i.definition.name}{'  [equipped]' if i.equipped else ''}"
        _inv_parts.append(line)
        for _child in _contained.get(i.item_id, []):
            _cdefn = ITEM_REGISTRY.get(_child.item_id)
            _cname = _cdefn.name if _cdefn else _child.item_id
            if _child.charges is not None and _cdefn is not None and hasattr(_cdefn, "maxCharges"):
                if _cdefn.maxCharges < 0:
                    _charges = " (\u221e)"
                else:
                    _charges = f" ({_child.charges}/{_cdefn.maxCharges})"
            else:
                _charges = ""
            _inv_parts.append(f"    \u2514 {_cname}{_charges}")
    inv_lines = "\n".join(_inv_parts) if _inv_parts else "  (empty)"

    # Equipped slots summary
    slot_lines = []
    for slot in UI_SLOTS:
        item_id = char.equipped_slots.get(slot.value)
        if item_id:
            defn = ITEM_REGISTRY.get(item_id)
            item_name = defn.name if defn else item_id
        else:
            item_name = "(empty)"
        slot_lines.append(f"  {_SLOT_LABELS[slot]}: {item_name}")

    st = char.saving_throws
    sheet_lines = [
        sep,
        f"{char.name}  \u2014  {char.character_class.value} Level {char.level}{leader_note}",
        f"HP: {char.hp_current}/{char.hp_max}   DEF: {char.defense} RES: {char.resistance}  Move: {char.movement_speed}'",
        f"XP: {char.experience}   Gold: {char.gold} gp",
        sep,
        f"PHY {a.physique:+d}   FNS {a.finesse:+d}",
        f"RSN {a.reason:+d}   SVY {a.savvy:+d}",
        sep,
        "Saves:",
        "  PHY: {}   FNS: {}".format(
            st.get("save", 0) + a.physique,
            st.get("save", 0) + a.finesse,
        ),
        "  RSN: {}   SVY: {}".format(
            st.get("save", 0) + a.reason,
            st.get("save", 0) + a.savvy,
        ),
        sep,
        "Equipped:",
        *slot_lines,
        sep,
        f"Inventory ({char.slots_used}/{char.inventory_size} slots):",
        inv_lines,
        sep,
    ]
    if char.status_notes:
        sheet_lines.append(f"Status: {char.status_notes}")

    return "```\n{}\n```".format("\n".join(sheet_lines))


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class EquipSelectView(discord.ui.View):
    """
    Shown when the player taps "Equip Item".
    Presents a Select menu listing equippable items from the inventory.
    Choosing an item either equips it directly (if the slot is unambiguous)
    or, for accessories, asks which slot.
    """

    def __init__(self, char, state, channel_id: str):
        super().__init__(timeout=120)
        self.char = char
        self.state = state
        self.channel_id = channel_id

        # Build options: only items that are EquipItem definitions and not
        # already flagged as equipped.
        options = []
        for inv in char.inventory:
            if inv.container_id:
                continue  # contained spells are not directly equippable
            defn = ITEM_REGISTRY.get(inv.item_id)
            if defn is None or not isinstance(defn, EquipItem):
                continue
            label = defn.name
            if inv.equipped:
                label += " ✓"
            options.append(discord.SelectOption(
                label=label[:100],
                value=inv.item_id,
                description=f"{type(defn).__name__}",
            ))

        if not options:
            options = [discord.SelectOption(label="(no equippable items)", value="__none__")]

        select = discord.ui.Select(
            placeholder="Choose an item to equip…",
            options=options[:25],  # Discord maximum
        )
        select.callback = self._on_select
        self.add_item(select)

        back = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back.callback = self._on_back
        self.add_item(back)

    async def _on_select(self, interaction: discord.Interaction):
        item_id = interaction.data["values"][0]
        if item_id == "__none__":
            await interaction.response.edit_message(
                content="You have no equippable items.", view=self
            )
            return

        defn = ITEM_REGISTRY.get(item_id)
        if defn is None:
            await interaction.response.edit_message(content="Unknown item.", view=None)
            return

        # Accessories get a slot-choice sub-view; everything else equips immediately.
        from engine.item import Gear
        if isinstance(defn, Gear) and defn.slot == "accessory":
            await interaction.response.edit_message(
                content=f"Which accessory slot for **{defn.name}**?",
                view=AccessorySlotView(self.char, self.state, self.channel_id, item_id),
            )
            return

        result = equip_item(self.state, self.char.character_id, item_id)
        await save_session_async(self.state)
        if result.ok:
            await interaction.response.edit_message(
                content=f"✅ {result.message}",
                view=EquipMenuView(self.char, self.state, self.channel_id),
            )
        else:
            await interaction.response.edit_message(
                content=f"❌ {result.error}",
                view=EquipMenuView(self.char, self.state, self.channel_id),
            )

    async def _on_back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=_character_sheet(self.char, self.state),
            view=EquipMenuView(self.char, self.state, self.channel_id),
        )


class AccessorySlotView(discord.ui.View):
    """Let the player pick ACCESSORY1 or ACCESSORY2."""

    def __init__(self, char, state, channel_id: str, item_id: str):
        super().__init__(timeout=60)
        self.char = char
        self.state = state
        self.channel_id = channel_id
        self.item_id = item_id

        for slot in (ItemSlot.ACCESSORY1, ItemSlot.ACCESSORY2):
            occupied = char.equipped_slots.get(slot.value)
            occupied_name = ""
            if occupied:
                d = ITEM_REGISTRY.get(occupied)
                occupied_name = f" (currently: {d.name})" if d else " (occupied)"
            label = f"{_SLOT_LABELS[slot]}{occupied_name}"
            btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.primary)
            btn.callback = self._make_callback(slot)
            self.add_item(btn)

        back = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back.callback = self._on_back
        self.add_item(back)

    def _make_callback(self, slot: ItemSlot):
        async def callback(interaction: discord.Interaction):
            result = equip_item(self.state, self.char.character_id, self.item_id, slot=slot)
            await save_session_async(self.state)
            msg = f"✅ {result.message}" if result.ok else f"❌ {result.error}"
            await interaction.response.edit_message(
                content=msg,
                view=EquipMenuView(self.char, self.state, self.channel_id),
            )
        return callback

    async def _on_back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=_character_sheet(self.char, self.state),
            view=EquipMenuView(self.char, self.state, self.channel_id),
        )


class UnequipView(discord.ui.View):
    """
    Shown when the player taps "Unequip Item".
    Presents buttons for each occupied slot.
    """

    def __init__(self, char, state, channel_id: str):
        super().__init__(timeout=120)
        self.char = char
        self.state = state
        self.channel_id = channel_id

        any_equipped = False
        for slot in UI_SLOTS:
            item_id = char.equipped_slots.get(slot.value)
            if item_id is None:
                continue
            any_equipped = True
            defn = ITEM_REGISTRY.get(item_id)
            item_name = defn.name if defn else item_id
            label = f"{_SLOT_LABELS[slot]}: {item_name}"
            btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.danger)
            btn.callback = self._make_callback(slot)
            self.add_item(btn)

        if not any_equipped:
            placeholder = discord.ui.Button(
                label="Nothing equipped", style=discord.ButtonStyle.secondary, disabled=True
            )
            self.add_item(placeholder)

        back = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back.callback = self._on_back
        self.add_item(back)

    def _make_callback(self, slot: ItemSlot):
        async def callback(interaction: discord.Interaction):
            result = unequip_item(self.state, self.char.character_id, slot)
            await save_session_async(self.state)
            msg = f"✅ {result.message}" if result.ok else f"❌ {result.error}"
            await interaction.response.edit_message(
                content=msg,
                view=EquipMenuView(self.char, self.state, self.channel_id),
            )
        return callback

    async def _on_back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=_character_sheet(self.char, self.state),
            view=EquipMenuView(self.char, self.state, self.channel_id),
        )


class EquipMenuView(discord.ui.View):
    """
    Top-level equipment menu — Equip / Unequip / Done buttons.
    This is what the player sees immediately after the character sheet is sent.
    """

    def __init__(self, char, state, channel_id: str):
        super().__init__(timeout=300)
        self.char = char
        self.state = state
        self.channel_id = channel_id

    @discord.ui.button(label="Equip Item", style=discord.ButtonStyle.primary)
    async def equip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Choose an item from your inventory to equip:",
            view=EquipSelectView(self.char, self.state, self.channel_id),
        )

    @discord.ui.button(label="Unequip Item", style=discord.ButtonStyle.secondary)
    async def unequip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Choose a slot to unequip:",
            view=UnequipView(self.char, self.state, self.channel_id),
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=_character_sheet(self.char, self.state),
            view=None,
        )
