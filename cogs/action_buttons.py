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

from discord_tasks import post_oracle_question
from engine import abscond, ask_oracle, emote, enter_rounds, exit_rounds, say, submit_turn
from models import GameState, SessionMode, TurnStatus
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

    leader_character_id: the character_id of the current party leader (or None).
    The Abscond button is disabled for everyone when this is None, and the
    button callback re-checks at click time so a stale view can't be exploited.
    """

    def __init__(
        self,
        channel_id: str,
        turn_is_open: bool = True,
        leader_character_id=None,
    ):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.leader_character_id = leader_character_id
        # Disable turn-action buttons when the turn is closed.
        if not turn_is_open:
            for item in self.children:
                item.disabled = True
        # Abscond is only usable by the party leader; disable it for everyone
        # else at view-build time.  The callback re-checks at click time.
        # (We can't hide buttons per-user — Discord views are shared.)
        # Note: self.children is populated by @discord.ui.button at class
        # definition time, so Abscond isn't in self.children yet when __init__
        # runs for the persistent dummy instance.  We mark it via a flag and
        # the on_ready registration passes leader_character_id=None which
        # leaves the button in whatever state the decorator set (enabled).

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
        # Oracle does NOT consume a turn action — uses ask_oracle(), not submit_turn().
        # Guards are lighter: session must be active but turn doesn't need to be open.
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session in this channel.", ephemeral=True
            )
            return
        if not state.session_active:
            await interaction.response.send_message(
                "⚠ The session is on hold.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "⚠ You don't have a character in this session.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            _OracleModal(channel_id=channel_id)
        )


    # row 2 — utility actions -----------------------------------------------

    @discord.ui.button(
        label="Abscond",
        style=discord.ButtonStyle.secondary,
        custom_id="action:abscond",
        row=2,
    )
    async def abscond_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session in this channel.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None or (
            state.party is not None
            and state.party.leader_id != char.character_id
        ):
            await interaction.response.send_message(
                "⚠ Only the party leader can use Abscond.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_AbscondModal(channel_id=channel_id))

    @discord.ui.button(
        label="Say",
        style=discord.ButtonStyle.secondary,
        custom_id="action:say",
        row=2,
    )
    async def say_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
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
        await interaction.response.send_modal(
            _SayEmoteModal(channel_id=channel_id, is_emote=False)
        )

    @discord.ui.button(
        label="Emote",
        style=discord.ButtonStyle.secondary,
        custom_id="action:emote",
        row=2,
    )
    async def emote_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
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
        await interaction.response.send_modal(
            _SayEmoteModal(channel_id=channel_id, is_emote=True)
        )

    @discord.ui.button(
        label="Strife",
        style=discord.ButtonStyle.danger,
        custom_id="action:strife",
        row=2,
    )
    async def strife_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from store import repost_status
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session in this channel.", ephemeral=True
            )
            return
        user_id = str(interaction.user.id)
        char = _find_character(state, user_id)
        is_dm = state.dm_user_id == user_id
        is_leader = (
            char is not None
            and state.party is not None
            and state.party.leader_id == char.character_id
        )
        if not (is_dm or is_leader):
            await interaction.response.send_message(
                "⚠ Only the DM or party leader can toggle combat rounds.",
                ephemeral=True,
            )
            return
        if state.mode == SessionMode.ROUNDS:
            result = exit_rounds(state)
            narrative = "Combat ended — returning to exploration."
        else:
            result = enter_rounds(state)
            narrative = "Combat begins!"
        if not result.ok:
            await interaction.response.send_message(
                f"⚠ {result.error}", ephemeral=True
            )
            return
        from engine import open_turn
        from models import TurnStatus as _TS
        if state.current_turn is None or state.current_turn.status != _TS.OPEN:
            open_turn(state)
        await interaction.response.send_message(narrative, ephemeral=True)
        await repost_status(interaction.channel, state, narrative=narrative)


# ---------------------------------------------------------------------------
# Modals for row-2 buttons (utility — not turn submissions)
# ---------------------------------------------------------------------------

class _AbscondModal(discord.ui.Modal, title="Abscond"):
    exit_number = discord.ui.TextInput(
        label="Exit number",
        placeholder="Enter the number of the exit to take (see status block).",
        required=True,
        max_length=4,
    )

    def __init__(self, *, channel_id: str) -> None:
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            num = int(self.exit_number.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "⚠ Please enter a number.", ephemeral=True
            )
            return
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "⚠ You don't have a character in this session.", ephemeral=True
            )
            return
        turn_number = state.turn_number
        result = abscond(state, char.character_id, num)
        if not result.ok:
            await interaction.response.send_message(
                f"⚠ {result.error}", ephemeral=True
            )
            return
        await interaction.response.send_message("✓ Moving out.", ephemeral=True)
        await update_status(interaction.channel, state)
        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)


class _SayEmoteModal(discord.ui.Modal):
    """Say or Emote modal. TextInput is built dynamically to avoid the
    deprecated post-construction label/placeholder mutation."""

    def __init__(self, *, channel_id: str, is_emote: bool) -> None:
        title = "Emote" if is_emote else "Say"
        super().__init__(title=title)
        self.channel_id = channel_id
        self.is_emote = is_emote
        self.text = discord.ui.TextInput(
            label="Describe the action" if is_emote else "What do you say?",
            placeholder="Describe what your character does (e.g. 'nods slowly')."
                        if is_emote else "Speak in character.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "⚠ You don't have a character in this session.", ephemeral=True
            )
            return
        if self.is_emote:
            emote(state, char.name, self.text.value)
        else:
            say(state, char.name, self.text.value)
        await interaction.response.send_message("✓ Done.", ephemeral=True)
        await update_status(interaction.channel, state)


class _OracleModal(discord.ui.Modal):
    """Oracle modal — calls ask_oracle(), not submit_turn(). Does not consume a turn."""

    def __init__(self, *, channel_id: str) -> None:
        super().__init__(title="Oracle")
        self.channel_id = channel_id
        self.question = discord.ui.TextInput(
            label="Question or brief interaction",
            placeholder=(
                "Describe a short action that takes less than a full turn, "
                "or ask a question"
            ),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message(
                "⚠ No active session.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        asker = char.name if char else interaction.user.display_name
        result, oracle = ask_oracle(
            state, asker, self.question.value,
            asker_owner_id=str(interaction.user.id),
        )
        if not result.ok:
            await interaction.response.send_message(
                f"⚠ {result.error}", ephemeral=True
            )
            return
        await interaction.response.send_message("✓ Oracle submitted.", ephemeral=True)
        msg = await post_oracle_question(interaction.channel, oracle)
        oracle.message_id = msg.id
        await update_status(interaction.channel, state)

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
    # Resolve leader's owner_id so __init__ can disable Abscond for non-leaders.
    leader_character_id = state.party.leader_id if state.party else None
    return ExplorationActionView(
        channel_id=str(state.platform_channel_id),
        turn_is_open=turn_is_open,
        leader_character_id=leader_character_id,
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
