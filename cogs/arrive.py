"""
cogs/arrive.py — /arrive command for player character creation.

Flow:
  1. Player uses /arrive in the game channel.
  2. Bot DMs them their rolled stats and Accept/Reroll buttons.
  3. On Accept, bot sends job (class) selection buttons.
  4. On job select, character is created immediately — no loadout step.

Stats use the Azure system: four stats (Physique, Finesse, Reason, Savvy),
stored as large integers scaled by POWER_LEVEL.
"""

from __future__ import annotations

import contextlib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from engine import create_character, equip_item, give_item, roll_stats
from engine.data_loader import ITEM_REGISTRY
from engine.item import ChargeWeapon, EquipItem, Gear, Item, Weapon
from models import CharacterClass, SessionMode
from store import get_characters_by_owner, get_session, save_session, update_status
from validation import validate_character_name

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stat display helper
# ---------------------------------------------------------------------------

def _fmt_stats(stats: dict) -> str:
    """Format rolled Azure stats for display in a DM message."""
    return (
        f"**PHY** {stats['physique']:+d}   "
        f"**FNS** {stats['finesse']:+d}\n"
        f"**RSN** {stats['reason']:+d}   "
        f"**SVY** {stats['savvy']:+d}"
    )


# ---------------------------------------------------------------------------
# Shop helpers - list purchasable items by type/slot
# ---------------------------------------------------------------------------

def get_purchasable_items_by_slot() -> dict[str, list[tuple[str, str, int]]]:
    """
    Returns a dict mapping slot/type names to lists of (item_id, name, price) tuples.
    Only includes items marked as purchaseable=True in ITEM_REGISTRY.
    """
    from collections import defaultdict
    by_slot: dict[str, list[tuple[str, str, int]]] = defaultdict(list)

    for item_id, item in ITEM_REGISTRY.items():
        if not getattr(item, 'purchaseable', False):
            continue

        if isinstance(item, Gear):
            slot = getattr(item, 'slot', 'unknown')
            by_slot[slot].append((item_id, item.name, item.price))
        elif isinstance(item, (Weapon, ChargeWeapon)):
            by_slot['weapon'].append((item_id, item.name, item.price))
        elif isinstance(item, Item):
            by_slot['other'].append((item_id, item.name, item.price))

    # Sort each list by price, then by name
    for slot in by_slot:
        by_slot[slot].sort(key=lambda x: (x[2], x[1]))

    return dict(by_slot)


def format_items_list(slot: str) -> str:
    """
    Format a plaintext Discord list of all purchasable items for a given slot/type.
    Returns a formatted string suitable for embedding in a message.
    """
    items_by_slot = get_purchasable_items_by_slot()

    if slot not in items_by_slot:
        return f"No purchasable items found for **{slot}**."

    items = items_by_slot[slot]
    lines = [f"**Purchasable {slot.title()} Items:**\n"]

    for _item_id, name, price in items:
        lines.append(f"• **{name}** — {price} gp")

    return "\n".join(lines)


def get_available_slots() -> list[str]:
    """Returns a sorted list of available item slots/types with purchasable items."""
    items_by_slot = get_purchasable_items_by_slot()
    return sorted(items_by_slot.keys())


# ---------------------------------------------------------------------------
# Shop View - allows players to browse and buy items
# ---------------------------------------------------------------------------

class ItemSelectView(discord.ui.View):
    """
    View for selecting a specific item from a list after choosing a slot.
    Allows players to select an item and then click Buy to purchase it.
    """

    def __init__(self, channel_id: str, character_id: str, owner_id: str, slot: str, items: list[tuple[str, str, int]]):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.character_id = character_id
        self.owner_id = owner_id
        self.slot = slot
        self.items = items  # List of (item_id, name, price)
        self.selected_item_id: str | None = None

        # Add buttons for each item (up to Discord limit)
        for item_id, name, price in items[:24]:
            btn = discord.ui.Button(
                label=f"{name} ({price} gp)",
                style=discord.ButtonStyle.secondary,
                custom_id=f"item_{item_id}",
            )
            btn.callback = self._make_item_callback(item_id, name, price)
            self.add_item(btn)

        # Add Back button
        back_btn = discord.ui.Button(
            label="← Back to Slots",
            style=discord.ButtonStyle.primary,
            custom_id="back_to_slots",
        )
        back_btn.callback = self._back_callback
        self.add_item(back_btn)

    def _make_item_callback(self, item_id: str, name: str, price: int):
        async def callback(interaction: discord.Interaction):
            self.selected_item_id = item_id

            # Highlight selected item by disabling others
            for item in self.children:
                if hasattr(item, 'custom_id') and item.custom_id.startswith('item_'):
                    item.disabled = (item.custom_id != f"item_{item_id}")

            # Add Buy button if not already present
            has_buy = any(hasattr(i, 'custom_id') and i.custom_id == 'buy_item' for i in self.children)
            if not has_buy:
                buy_btn = discord.ui.Button(
                    label=f"✓ Buy {name} ({price} gp)",
                    style=discord.ButtonStyle.success,
                    custom_id="buy_item",
                )
                buy_btn.callback = self._buy_callback
                self.add_item(buy_btn)

            await interaction.response.edit_message(
                content=f"**Selected:** {name} ({price} gp)\n\nClick 'Buy' to purchase this item.",
                view=self,
            )

        return callback

    async def _buy_callback(self, interaction: discord.Interaction):
        log.debug(
            "_buy_callback: channel=%s character_id=%s selected_item=%s user=%s",
            self.channel_id, self.character_id, self.selected_item_id, interaction.user.id,
        )

        if self.selected_item_id is None:
            log.warning("_buy_callback: no item selected")
            await interaction.response.edit_message(content="⚠ No item selected.", view=self)
            return

        item = ITEM_REGISTRY.get(self.selected_item_id)
        if item is None:
            log.error("_buy_callback: item_id %r not found in ITEM_REGISTRY", self.selected_item_id)
            await interaction.response.edit_message(
                content="⚠ Item not found in registry.", view=self
            )
            return

        # Get the session and character
        state = get_session(self.channel_id)
        if state is None:
            log.error("_buy_callback: session not found for channel %s", self.channel_id)
            await interaction.response.edit_message(content="⚠ Session no longer exists.", view=self)
            return

        # state.characters is keyed by UUID objects; self.character_id is a str.
        # Convert to UUID before lookup.
        from uuid import UUID
        try:
            char_uuid = UUID(self.character_id)
        except (ValueError, AttributeError):
            log.error("_buy_callback: invalid character_id %r", self.character_id)
            await interaction.response.edit_message(content="⚠ Invalid character ID.", view=self)
            return

        log.debug(
            "_buy_callback: state has %d characters, keys=%s",
            len(state.characters),
            list(state.characters.keys()),
        )

        character = state.characters.get(char_uuid)
        if character is None:
            log.error(
                "_buy_callback: character UUID %s not found in state.characters (keys=%s)",
                char_uuid, list(state.characters.keys()),
            )
            await interaction.response.edit_message(content="⚠ Character not found.", view=self)
            return

        # Check if character has enough gold
        price = getattr(item, 'price', 0)
        if character.gold < price:
            log.info(
                "_buy_callback: insufficient gold — have %d, need %d", character.gold, price
            )
            await interaction.response.edit_message(
                content=f"⚠ Not enough gold! You have {character.gold} gp, but need {price} gp.",
                view=self,
            )
            return

        # Attempt to add item to inventory (enforces slot limit)
        result = give_item(state, char_uuid, self.selected_item_id)
        if not result.ok:
            await interaction.response.edit_message(
                content=f"⚠ {result.error}",
                view=self,
            )
            return

        # Deduct gold only after inventory check passes
        character.gold -= price
        log.info(
            "_buy_callback: purchased %r (%d gp) for character %s; gold remaining=%d",
            self.selected_item_id, price, char_uuid, character.gold,
        )

        # Save the session
        save_session(state)

        # Return to shop — offer equip prompt for equippable items
        remaining_gold = character.gold
        if isinstance(item, EquipItem) and getattr(item, 'slot', None):
            equip_view = EquipNowView(
                self.channel_id, self.character_id, self.owner_id,
                self.selected_item_id, item.name, remaining_gold,
            )
            await interaction.response.edit_message(
                content=(
                    f"✓ Purchased **{item.name}** for {price} gp!\n"
                    f"You have {remaining_gold} gp remaining.\n\n"
                    f"Would you like to equip **{item.name}** now?"
                ),
                view=equip_view,
            )
        else:
            shop_view = ShopView(self.channel_id, self.character_id, self.owner_id)
            await interaction.response.edit_message(
                content=(
                    f"✓ Purchased **{item.name}** for {price} gp!\n"
                    f"You have {remaining_gold} gp remaining.\n\n"
                    f"**Item Shop** — Select a category to continue shopping:"
                ),
                view=shop_view,
            )

    async def _back_callback(self, interaction: discord.Interaction):
        # Return to slot selection
        shop_view = ShopView(self.channel_id, self.character_id, self.owner_id)
        await interaction.response.edit_message(
            content="**Item Shop** — Select a category to browse items:",
            view=shop_view,
        )


class EquipNowView(discord.ui.View):
    """
    Shown after a successful purchase of an equippable item.
    Lets the player equip it immediately or keep it in inventory.
    """

    def __init__(self, channel_id: str, character_id: str, owner_id: str, item_id: str, item_name: str, remaining_gold: int):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.character_id = character_id
        self.owner_id = owner_id
        self.item_id = item_id
        self.item_name = item_name
        self.remaining_gold = remaining_gold

    @discord.ui.button(label="Equip Now", style=discord.ButtonStyle.success)
    async def equip_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        from uuid import UUID
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.edit_message(content="⚠ Session no longer exists.", view=None)
            return
        char_uuid = UUID(self.character_id)
        result = equip_item(state, char_uuid, self.item_id)
        if result.ok:
            save_session(state)
            prefix = f"✓ {result.message}\n\n"
        else:
            prefix = f"⚠ Could not equip: {result.error}\n\n"
        shop_view = ShopView(self.channel_id, self.character_id, self.owner_id)
        await interaction.response.edit_message(
            content=prefix + f"**Item Shop** — {self.remaining_gold} gp remaining. Select a category:",
            view=shop_view,
        )

    @discord.ui.button(label="Keep in Inventory", style=discord.ButtonStyle.secondary)
    async def keep_in_inventory(self, interaction: discord.Interaction, button: discord.ui.Button):
        shop_view = ShopView(self.channel_id, self.character_id, self.owner_id)
        await interaction.response.edit_message(
            content=f"**Item Shop** — {self.remaining_gold} gp remaining. Select a category:",
            view=shop_view,
        )


class ShopView(discord.ui.View):
    """
    View for browsing purchasable items by slot/type.
    Players can select a slot to see available items and their prices.
    """

    def __init__(self, channel_id: str, character_id: str, owner_id: str):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.character_id = character_id
        self.owner_id = owner_id
        self.selected_slot: str | None = None

        # Add buttons for each available slot
        slots = get_available_slots()
        for slot in slots[:24]:  # Discord limit of 25 buttons per view
            btn = discord.ui.Button(
                label=slot.title(),
                style=discord.ButtonStyle.secondary,
                custom_id=f"slot_{slot}",
            )
            btn.callback = self._make_slot_callback(slot)
            self.add_item(btn)

    def _make_slot_callback(self, slot: str):
        async def callback(interaction: discord.Interaction):
            self.selected_slot = slot
            items_by_slot = get_purchasable_items_by_slot()
            items = items_by_slot.get(slot, [])

            if not items:
                await interaction.response.edit_message(
                    content=f"No purchasable items found for **{slot}**.",
                    view=self,
                )
                return

            # Show item selection view
            item_view = ItemSelectView(
                self.channel_id,
                self.character_id,
                self.owner_id,
                slot,
                items,
            )

            items_preview = "\n".join([f"• **{name}** — {price} gp" for _, name, price in items[:10]])
            if len(items) > 10:
                items_preview += f"\n... and {len(items) - 10} more"

            await interaction.response.edit_message(
                content=f"**{slot.title()} Items**\n\n{items_preview}\n\nSelect an item to purchase:",
                view=item_view,
            )

        return callback


# ---------------------------------------------------------------------------
# Step 2: Job selection view
# ---------------------------------------------------------------------------

class JobView(discord.ui.View):
    """
    Second step: the player picks their job (character class).
    Character is created immediately on selection — no loadout step.
    """

    def __init__(self, channel_id: str, character_name: str, stats: dict, owner_id: str):
        super().__init__(timeout=300)
        self.channel_id     = channel_id
        self.character_name = character_name
        self.stats          = stats
        self.owner_id       = owner_id

        for cls in CharacterClass:
            btn = discord.ui.Button(
                label=cls.value,
                style=discord.ButtonStyle.primary,
                custom_id=f"job_{cls.name}",
            )
            btn.callback = self._make_callback(cls)
            self.add_item(btn)

    def _make_callback(self, character_class: CharacterClass):
        async def callback(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Job chosen: **{character_class.value}**. Creating character…",
                view=self,
            )

            try:
                state = get_session(self.channel_id)
                if state is None:
                    await interaction.followup.send(
                        "⚠ Session no longer exists.", ephemeral=True
                    )
                    return

                result = create_character(
                    state=state,
                    name=self.character_name,
                    character_class=character_class,
                    equipment_package="",
                    owner_id=self.owner_id,
                    prerolled_stats=self.stats,
                )

                if not result.ok:
                    await interaction.followup.send(
                        f"⚠ Error creating character: {result.error}", ephemeral=True
                    )
                    return

                # Auto-assign leader if none set
                if state.party.leader_id is None:
                    state.party.leader_id = state.party.member_ids[-1]

                # Get the newly created character (last added to party member_ids)
                new_char_id = state.party.member_ids[-1]
                new_char = state.characters.get(new_char_id)
                if new_char is None:
                    await interaction.followup.send(
                        "⚠ Character created but could not be retrieved.", ephemeral=True
                    )
                    return

                save_session(state)

                await interaction.followup.send(
                    f"✓ **{self.character_name}** the {character_class.value} has arrived!",
                    ephemeral=False,
                )

                channel = interaction.client.get_channel(int(self.channel_id))
                if channel is None:
                    try:
                        channel = await interaction.client.fetch_channel(int(self.channel_id))
                    except discord.HTTPException:
                        channel = None
                if channel is not None:
                    await update_status(channel, state)

                # Now present the item shop view in DMs
                dm_channel = await interaction.user.create_dm()
                shop_view = ShopView(self.channel_id, str(new_char_id), self.owner_id)
                await dm_channel.send(
                    f"**Welcome to the Item Shop!**\n\n"
                    f"You can browse and purchase starting equipment for **{self.character_name}**.\n"
                    f"Select a category to see available items:",
                    view=shop_view,
                )

            except Exception as exc:
                with contextlib.suppress(discord.HTTPException):
                    await interaction.followup.send(
                        f"⚠ Something went wrong: {exc}", ephemeral=True
                    )
                raise

        return callback


# ---------------------------------------------------------------------------
# Step 1: Character selection view (for users with existing characters)
# ---------------------------------------------------------------------------

class CharacterSelectionView(discord.ui.View):
    """
    First step for users with existing characters:
    Choose an existing character or create a new one.
    """

    def __init__(self, channel_id: str, owner_id: str, existing_chars: list):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.existing_chars = existing_chars

        # Add buttons for each existing character
        for char in existing_chars[:24]:  # Discord limit of 25 buttons per row, leave room for "New" button
            btn = discord.ui.Button(
                label=f"{char.name} ({char.character_class.value})",
                style=discord.ButtonStyle.secondary,
                custom_id=f"char_{char.character_id}",
            )
            btn.callback = self._make_character_callback(char)
            self.add_item(btn)

        # Add "Create New Character" button
        new_btn = discord.ui.Button(
            label="Create New Character",
            style=discord.ButtonStyle.success,
            custom_id="new_character",
        )
        new_btn.callback = self._new_character_callback
        self.add_item(new_btn)

    def _make_character_callback(self, character):
        async def callback(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Selected character: **{character.name}**. Importing into session...",
                view=self,
            )

            try:
                state = get_session(self.channel_id)
                if state is None:
                    await interaction.followup.send("Session no longer exists.",
                            ephemeral=True
                            )
                    return

                # Check if character is already in this session
                if character.character_id in state.characters:
                    await interaction.followup.send(
                        f"**{character.name}** is already in this session.", ephemeral=True
                    )
                    return

                # Import the existing character into the session
                state.characters[character.character_id] = character
                if state.party is not None:
                    state.party.member_ids.append(character.character_id)

                save_session(state)

                await interaction.followup.send(
                    f"**{character.name}** the {character.character_class.value} has arrived! "
                    f"Head back to the game channel.",
                    ephemeral=False,
                )

                channel = interaction.client.get_channel(int(self.channel_id))
                if channel is None:
                    try:
                        channel = await interaction.client.fetch_channel(int(self.channel_id))
                    except discord.HTTPException:
                        channel = None
                if channel is not None:
                    await update_status(channel, state)

            except Exception as exc:
                with contextlib.suppress(discord.HTTPException):
                    await interaction.followup.send(
                        f"Something went wrong: {exc}", ephemeral=True
                    )
                raise

        return callback

    async def _new_character_callback(self, interaction: discord.Interaction):
        # Prompt for character name via modal - must be the first response
        modal = CharacterNameModal(
                channel_id=self.channel_id,
                owner_id=self.owner_id,
        )

        await interaction.response.send_modal(modal)


class CharacterNameModal(discord.ui.Modal, title="Enter Character Name"):
    """Modal to collect character name for new character creation."""

    def __init__(self, channel_id: str, owner_id: str):
        super().__init__()
        self.channel_id = channel_id
        self.owner_id = owner_id

        self.name_input = discord.ui.TextInput(
                label="Character Name",
                placeholder="Enter your character's name",
                min_length=1,
                max_length=50,
                )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        character_name = self.name_input.value.strip()
        vresult = validate_character_name(character_name)
        if not vresult:
            await interaction.response.send_message(
                    vresult.error, ephemeral=True
                    )
            return

        # Start the stat rolling process for new character creation
        try:
            dm_channel = await interaction.user.create_dm()
            stat_view = StatRollView(
                    channel_id=self.channel_id,
                    character_name=character_name,
                    owner_id=self.owner_id,
                    )
            await dm_channel.send(stat_view._stats_message(), view=stat_view)
            await interaction.response.send_message(
                    "Check your DMs to roll stats and choose your class!",
                    ephemeral=True,
                    )
        except discord.Forbidden:
            await interaction.response.send_message(
                    "I couldn't DM you. Please enable DMs from server members and try again.",
                    ephemeral=True,
                    )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message(
                f"Something went wrong: {error}", ephemeral=True
                )



# ---------------------------------------------------------------------------
# Step 2: Stat roll view (Accept / Reroll)
# ---------------------------------------------------------------------------

class StatRollView(discord.ui.View):
    def __init__(self, channel_id: str, character_name: str, owner_id: str):
        super().__init__(timeout=300)
        self.channel_id     = channel_id
        self.character_name = character_name
        self.owner_id       = owner_id
        self.stats          = roll_stats()

    def _stats_message(self) -> str:
        return (
            f"**{self.character_name}** — rolled stats:\n\n"
            f"{_fmt_stats(self.stats)}\n\n"
            "Accept these stats or reroll?"
        )

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=(
                f"Stats accepted!\n\n"
                f"{_fmt_stats(self.stats)}\n\n"
                "Now choose your job:"
            ),
            view=self,
        )
        job_view = JobView(
            channel_id=self.channel_id,
            character_name=self.character_name,
            stats=self.stats,
            owner_id=self.owner_id,
        )
        await interaction.followup.send("Choose your job:", view=job_view)

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.danger)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stats = roll_stats()
        await interaction.response.edit_message(content=self._stats_message(), view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ArriveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="arrive",
        description="Join the session and create your character.",
    )
    async def arrive(self, interaction: discord.Interaction):
        channel_id = str(interaction.channel_id)

        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No session in this channel. Ask the DM to create one.",
                ephemeral=True,
            )
            return

        if state.mode != SessionMode.PRE_START:
            await interaction.response.send_message(
                "The session has already started. New characters cannot join mid-session.",
                ephemeral=True,
            )
            return

        owner_id = str(interaction.user.id)

        # Check if this user already has a character in the session
        for char in state.characters.values():
            if char.owner_id == owner_id:
                await interaction.response.send_message(
                    f"You already have a character (**{char.name}**) in this session.",
                    ephemeral=True,
                )
                return

        # Check for existing characters owned by this user
        existing_chars = get_characters_by_owner(owner_id)

        if existing_chars:
            # Present character selection view
            try:
                dm_channel = await interaction.user.create_dm()
                view = CharacterSelectionView(
                    channel_id=channel_id,
                    owner_id=owner_id,
                    existing_chars=existing_chars,
                )
                await dm_channel.send(
                    "You have existing characters. Would you like to select one or create a new character?",
                    view=view,
                )
                await interaction.response.send_message(
                    "Check your DMs to select an existing character or create a new one!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I couldn't DM you. Please enable DMs from server members and try again.",
                    ephemeral=True,
                )
                return
        else:
            # No existing characters - prompt for character name via modal directly
            modal = CharacterNameModal(
                    channel_id=channel_id,
                    owner_id=owner_id,
                    )
            await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(ArriveCog(bot))
