"""
cogs/action_buttons.py — In-channel action buttons attached to the status message.

Buttons appear below the status block in EXPLORATION mode and present the
player with an ephemeral modal (popup visible only to them) that pre-prompts
the specific action type, then submits the result directly to submit_turn().

Adding a new button
-------------------
1. Define a Modal subclass (or use ActionModal with appropriate arguments).
2. Add a @discord.ui.button() method to ExplorationActionView that calls
   interaction.response.send_modal(...).
3. Add the button to the test in _build_view() if mode-gating is needed.

No changes to engine.py, models.py, or persistence.py are needed — this is
purely a platform-layer addition.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from engine import submit_turn
from models import SessionMode, TurnStatus
from store import (
    get_session,
    notify_dm_of_turn_close,
    update_status,
)

# ---------------------------------------------------------------------------
# Helper: find a character by Discord user ID
# ---------------------------------------------------------------------------

def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


# ---------------------------------------------------------------------------
# Generic action modal
# ---------------------------------------------------------------------------

class ActionModal(discord.ui.Modal):
    """
    A single-field modal that prompts the player for action details,
    then calls submit_turn() and updates the status message.

    Parameters
    ----------
    title        : Modal window title shown to the player.
    input_label  : Label text above the text box.
    placeholder  : Grey hint text inside the text box.
    channel_id   : The game channel this action belongs to.
    action_prefix: Prepended to the player's text before submit_turn().
                   E.g. "Search: " so the DM's status view is clean.
    """

    def __init__(
        self,
        title: str,
        input_label: str,
        placeholder: str,
        channel_id: str,
        action_prefix: str,
    ):
        super().__init__(title=title, timeout=300)
        self.channel_id = channel_id
        self.action_prefix = action_prefix

        self.detail = discord.ui.TextInput(
            label=input_label,
            placeholder=placeholder,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.detail)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session in this channel.", ephemeral=True
            )
            return

        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "⚠ You don't have a character in this session.", ephemeral=True
            )
            return

        if state.mode == SessionMode.ROUNDS:
            await interaction.response.send_message(
                "⚠ Combat is active — use /round to submit your action.",
                ephemeral=True,
            )
            return

        if not state.session_active:
            await interaction.response.send_message(
                "⚠ The session is currently on hold.", ephemeral=True
            )
            return

        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            await interaction.response.send_message(
                "⚠ No open turn right now — the DM needs to resolve the previous turn first.",
                ephemeral=True,
            )
            return

        action_text = f"{self.action_prefix}{self.detail.value}"
        turn_number = state.turn_number

        result = submit_turn(state, char.character_id, action_text)
        if not result.ok:
            await interaction.response.send_message(
                f"⚠ {result.error}", ephemeral=True
            )
            return

        # Acknowledge the modal submission to Discord (required within 3 s)
        await interaction.response.send_message("Turn submitted!", ephemeral=True)

        # Update the status block in the channel
        channel = interaction.channel
        if channel is not None:
            await update_status(channel, state)
            if result.notify_dm:
                await notify_dm_of_turn_close(channel, state, turn_number)


# ---------------------------------------------------------------------------
# Guard helper
# ---------------------------------------------------------------------------

async def _check_turn(interaction: discord.Interaction):
    """
    Validate that a button click can proceed to show a modal.

    Returns (state, char) on success, or (None, None) after sending an
    ephemeral error if any guard fails.  All button callbacks call this
    before interaction.response.send_modal() so the guard logic lives in
    one place.
    """
    channel_id = str(interaction.channel_id)
    state = get_session(channel_id)

    if state is None:
        await interaction.response.send_message(
            "⚠ No active session in this channel.", ephemeral=True
        )
        return None, None

    if not state.session_active:
        await interaction.response.send_message(
            "⚠ The session is on hold.", ephemeral=True
        )
        return None, None

    if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
        await interaction.response.send_message(
            "⚠ The turn is closed — waiting for DM resolution.", ephemeral=True
        )
        return None, None

    char = _find_character(state, str(interaction.user.id))
    if char is None:
        await interaction.response.send_message(
            "⚠ You don't have a character in this session.", ephemeral=True
        )
        return None, None

    return state, char


# ---------------------------------------------------------------------------
# Exploration action view
# ---------------------------------------------------------------------------

class ExplorationActionView(discord.ui.View):
    """
    Buttons attached to the status message during EXPLORATION mode.

    Each button opens an ActionModal specific to that action type.
    The view uses timeout=None so buttons do not expire between status reposts.
    Buttons are visually disabled while the turn is closed (awaiting DM
    resolution) so players know to wait — they are re-enabled automatically
    when the next status message is posted with a fresh view instance.
    """

    def __init__(self, channel_id: str, turn_is_open: bool = True):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        # Disable all buttons when the turn is closed; re-enabled on next post.
        if not turn_is_open:
            for item in self.children:
                item.disabled = True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    # row 0 ----------------------------------------------------------------

    @discord.ui.button(
        label="Search",
        style=discord.ButtonStyle.secondary,
        custom_id="action:search",
        row=0,
    )
    async def search(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Search",
            input_label="Describe your search",
            placeholder=(
                "A 10×10 area. You can specify a feature from the list. "
                "More detail can lead to automatic success."
            ),
            channel_id=str(interaction.channel_id),
            action_prefix="Search: ",
        ))

    @discord.ui.button(
        label="Disarm Trap",
        style=discord.ButtonStyle.secondary,
        custom_id="action:disarm_trap",
        row=0,
    )
    async def disarm_trap(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Disarm Trap",
            input_label="Describe the trap and your approach",
            placeholder="More detail may lead to automatic success.",
            channel_id=str(interaction.channel_id),
            action_prefix="Disarm Trap: ",
        ))

    @discord.ui.button(
        label="Listen",
        style=discord.ButtonStyle.secondary,
        custom_id="action:listen",
        row=0,
    )
    async def listen(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Listen",
            input_label="What are you listening for or at?",
            placeholder="Typically used to listen at doors",
            channel_id=str(interaction.channel_id),
            action_prefix="Listen: ",
        ))

    @discord.ui.button(
        label="Force Open Door",
        style=discord.ButtonStyle.secondary,
        custom_id="action:force_door",
        row=0,
    )
    async def force_open_door(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Force Open Door",
            input_label="Which door, and how?",
            placeholder="Specify an exit number.",
            channel_id=str(interaction.channel_id),
            action_prefix="Force Open Door: ",
        ))

    # row 1 ----------------------------------------------------------------

    @discord.ui.button(
        label="Pick Lock",
        style=discord.ButtonStyle.secondary,
        custom_id="action:pick_lock",
        row=1,
    )
    async def pick_lock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Pick Lock",
            input_label="Which lock?",
            placeholder=(
                "Specify an exit number or a room feature (e.g, a locked chest), "
                "describe how you pick the lock"
            ),
            channel_id=str(interaction.channel_id),
            action_prefix="Pick Lock: ",
        ))

    @discord.ui.button(
        label="Craft",
        style=discord.ButtonStyle.secondary,
        custom_id="action:craft",
        row=1,
    )
    async def craft(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Craft",
            input_label="What are you making or repairing?",
            placeholder="Describe your action",
            channel_id=str(interaction.channel_id),
            action_prefix="Craft: ",
        ))

    @discord.ui.button(
        label="Other Turn Action",
        style=discord.ButtonStyle.secondary,
        custom_id="action:other_turn",
        row=1,
    )
    async def other_turn_action(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Other Action",
            input_label="Describe your action",
            placeholder="Anything that takes a full 10-minute dungeon turn.",
            channel_id=str(interaction.channel_id),
            action_prefix="",
        ))

    @discord.ui.button(
        label="Oracle",
        style=discord.ButtonStyle.primary,
        custom_id="action:oracle",
        row=1,
    )
    async def oracle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Oracle",
            input_label="Question or brief interaction",
            placeholder=(
                "Describe a short action that takes less than a full turn, "
                "or ask a question"
            ),
            channel_id=str(interaction.channel_id),
            action_prefix="Oracle: ",
        ))


# ---------------------------------------------------------------------------
# View factory — called by store.py
# ---------------------------------------------------------------------------

def build_action_view(state) -> discord.ui.View | None:
    """
    Return the appropriate action view for the current session state,
    or None if no buttons should be shown (PRE_START, on hold, etc.).

    Called by store._post_fresh_status() and store.update_status().
    """
    if state is None:
        return None
    if state.mode != SessionMode.EXPLORATION:
        return None
    if not state.session_active:
        return None

    turn_is_open = (
        state.current_turn is not None
        and state.current_turn.status == TurnStatus.OPEN
    )
    return ExplorationActionView(
        channel_id=str(state.platform_channel_id),
        turn_is_open=turn_is_open,
    )


# ---------------------------------------------------------------------------
# Cog (no slash commands — just registers the module as a cog for load_extension)
# ---------------------------------------------------------------------------

class ActionButtonsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Re-register the persistent view so buttons on pre-existing status
        # messages keep working after a bot restart.  discord.py requires
        # one registered instance per View class; channel_id is a dummy here
        # because every button callback derives it from interaction.channel_id
        # at click time instead of from self.channel_id.
        self.bot.add_view(ExplorationActionView(channel_id="__persistent__"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActionButtonsCog(bot))
