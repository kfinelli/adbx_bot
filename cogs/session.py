"""
cogs/session.py — In-game player slash commands.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from discord_tasks import post_oracle_question
from engine import (
    abscond,
    ask_oracle,
    emote,
    enter_rounds,
    equip_item,
    exit_rounds,
    open_turn,
    say,
    submit_turn,
    unequip_item,
)
from engine.azure_constants import ACCESSORY_SLOTS, ItemSlot, UI_SLOTS
from engine.data_loader import ITEM_REGISTRY
from engine.item import EquipItem
from models import SessionMode, TurnStatus
from store import (
    ack,
    ack_done,
    ack_err,
    get_session,
    notify_dm_of_turn_close,
    repost_status,
    save_session_async,
    update_status,
)

HELP_TEXT = (
    "Interact using slash commands.  Try `/turn` to perform actions that typically "
    "take a full dungeon turn (e.g., searching, listening, disarming traps).  Try "
    "`/oracle` to ask questions about the environment or perform simple interactions "
    "like retrieving items and opening doors. To move through an open exit to another "
    "room, try `/abscond`.\n"
    "When in rounds, `/round` is used to issue commands instead of `/turn`."
)

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
# Equipment management views
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
    This is what the player sees immediately after /character sends the DM.
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


# ---------------------------------------------------------------------------
# Character sheet renderer
# ---------------------------------------------------------------------------

def _character_sheet(char, state) -> str:
    """Produce the character sheet code block string."""
    a = char.ability_scores
    leader_note = " (Party Leader)" if (
        state.party and state.party.leader_id == char.character_id
    ) else ""
    sep = "\u2500" * 32

    inv_lines = "\n".join(
        f"  {i.quantity}x {i.definition.name}{'  [equipped]' if i.equipped else ''}"
        for i in char.inventory
    ) if char.inventory else "  (empty)"

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
        f"PHY {a.physique / 100:+.2f}   FNS {a.finesse / 100:+.2f}",
        f"RSN {a.reason   / 100:+.2f}   SVY {a.savvy   / 100:+.2f}",
        sep,
        "Saves:",
        "  Death/Poison:    {}".format(st.get("death_poison", "?")),
        "  Wands:           {}".format(st.get("wands", "?")),
        "  Paralysis/Stone: {}".format(st.get("paralysis_stone", "?")),
        "  Breath Weapon:   {}".format(st.get("breath_weapon", "?")),
        "  Spells:          {}".format(st.get("spells", "?")),
        sep,
        "Equipped:",
        *slot_lines,
        sep,
        "Inventory:",
        inv_lines,
        sep,
    ]
    if char.status_notes:
        sheet_lines.append(f"Status: {char.status_notes}")

    return "```\n{}\n```".format("\n".join(sheet_lines))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SessionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /help
    # ------------------------------------------------------------------

    @app_commands.command(name="help", description="Show instructions for new players.")
    async def help_cmd(self, interaction: discord.Interaction):
        await ack(interaction)
        await interaction.edit_original_response(content=HELP_TEXT)

    # ------------------------------------------------------------------
    # /character
    # ------------------------------------------------------------------

    @app_commands.command(name="character", description="Show your character sheet and manage equipment.")
    async def character_cmd(self, interaction: discord.Interaction):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await ack_err(interaction, "You don't have a character in this session.")
            return

        sheet = _character_sheet(char, state)

        # Send as a DM with the equip/unequip menu attached.
        try:
            dm_channel = await interaction.user.create_dm()
            await dm_channel.send(
                content=sheet,
                view=EquipMenuView(char, state, str(interaction.channel_id)),
            )
            await interaction.edit_original_response(
                content="📬 Your character sheet has been sent to your DMs."
            )
        except discord.Forbidden:
            # Fall back to in-channel (ephemeral-ish) if DMs are closed.
            await interaction.edit_original_response(content=sheet)

    # ------------------------------------------------------------------
    # /turn
    # ------------------------------------------------------------------

    @app_commands.command(name="turn", description="Describe what your character does this turn.")
    @app_commands.describe(action="What does your character do?")
    async def turn(self, interaction: discord.Interaction, action: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await ack_err(interaction, "You don't have a character in this session. Use /arrive first.")
            return
        if state.mode == SessionMode.ROUNDS:
            await ack_err(interaction, "Combat is active \u2014 use /round to submit your action.")
            return
        turn_number = state.turn_number
        result = submit_turn(state, char.character_id, action)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await ack_done(interaction)
        await update_status(interaction.channel, state)
        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    # ------------------------------------------------------------------
    # /round
    # ------------------------------------------------------------------

    @app_commands.command(name="round", description="Submit your action for the current combat round.")
    @app_commands.describe(action="What does your character do this round?")
    async def round_cmd(self, interaction: discord.Interaction, action: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return
        if state.mode != SessionMode.ROUNDS:
            await ack_err(interaction, "No combat active \u2014 use /turn to submit your action.")
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await ack_err(interaction, "You don't have a character in this session.")
            return
        turn_number = state.turn_number
        result = submit_turn(state, char.character_id, action)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await ack_done(interaction)
        await update_status(interaction.channel, state)
        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    # ------------------------------------------------------------------
    # /abscond
    # ------------------------------------------------------------------

    @app_commands.command(name="abscond", description="[Party leader] Move the party through a numbered exit.")
    @app_commands.describe(exit_number="The number of the exit to take (see status block)")
    async def abscond_cmd(self, interaction: discord.Interaction, exit_number: int):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await ack_err(interaction, "You don't have a character in this session.")
            return
        turn_number = state.turn_number
        result = abscond(state, char.character_id, exit_number)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await ack_done(interaction)
        await update_status(interaction.channel, state)
        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    # ------------------------------------------------------------------
    # /say
    # ------------------------------------------------------------------

    @app_commands.command(name="say", description="Your character says something aloud.")
    @app_commands.describe(text="What does your character say?")
    async def say_cmd(self, interaction: discord.Interaction, text: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session.")
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await ack_err(interaction, "You don't have a character in this session.")
            return
        say(state, char.name, text)
        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /emote
    # ------------------------------------------------------------------

    @app_commands.command(name="emote", description="Describe your character doing something.")
    @app_commands.describe(text="What does your character do? (e.g. 'nods respectfully')")
    async def emote_cmd(self, interaction: discord.Interaction, text: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session.")
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await ack_err(interaction, "You don't have a character in this session.")
            return
        emote(state, char.name, text)
        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /oracle
    # ------------------------------------------------------------------

    @app_commands.command(name="oracle", description="Ask the DM a question out of character.")
    @app_commands.describe(question="Your question for the DM")
    async def oracle_cmd(self, interaction: discord.Interaction, question: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session.")
            return
        char = _find_character(state, str(interaction.user.id))
        asker = char.name if char else interaction.user.display_name
        result = ask_oracle(
            state, asker, question, asker_owner_id=str(interaction.user.id)
        )
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        oracle = result.data
        await ack_done(interaction)
        msg = await post_oracle_question(interaction.channel, oracle)
        oracle.message_id = msg.id
        await save_session_async(state)

    # ------------------------------------------------------------------
    # /strife
    # ------------------------------------------------------------------

    @app_commands.command(name="strife", description="Toggle combat rounds mode (DM or party leader only).")
    async def strife(self, interaction: discord.Interaction):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return
        user_id = str(interaction.user.id)
        is_dm = state.dm_user_id == user_id
        char = _find_character(state, user_id)
        is_leader = (
            char is not None and
            state.party is not None and
            state.party.leader_id == char.character_id
        )
        if not (is_dm or is_leader):
            await ack_err(interaction, "Only the DM or party leader can toggle combat rounds.")
            return
        if state.mode == SessionMode.ROUNDS:
            result = exit_rounds(state)
            label = "Combat ended \u2014 returning to exploration."
        else:
            result = enter_rounds(state)
            label = "Combat begins!"
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            open_turn(state)
        await ack_done(interaction)
        await repost_status(interaction.channel, state, narrative=label)

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------

    @app_commands.command(name="status", description="Repost the current game status at the bottom of the channel.")
    async def status(self, interaction: discord.Interaction):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return
        await ack_done(interaction)
        await repost_status(interaction.channel, state)


def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))

