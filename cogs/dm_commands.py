"""
cogs/dm_commands.py — DM-facing slash commands.

All responses are ephemeral (errors only) or silent.
The channel stays clean — feedback comes from the status block updating.

Commands:
  /dm_newsession  — create a new session in this channel
  /dm_strife      — toggle combat rounds mode
  /dm_say         — post a message as a speaker
  /dm_emote       — post an emote message
"""

from __future__ import annotations

import contextlib

import discord
from discord import app_commands
from discord.ext import commands

from engine import (
    emote,
    enter_rounds,
    exit_rounds,
    open_turn,
    say,
)
from models import (
    SessionMode,
    TurnStatus,
)
from store import (
    ack,
    ack_done,
    ack_err,
    repost_status,
    require_session,
    update_status,
)


@contextlib.asynccontextmanager
async def dm_command_context(interaction: discord.Interaction, cog, allow_on_hold: bool = False):
    """Context manager for DM command boilerplate.

    Yields the session state if the user is authorized, else None.
    On successful completion, acknowledges done and updates/reposts status.
    On error, sends an ephemeral error message.
    """
    state = await cog._require_dm(interaction, allow_on_hold)
    if state is None:
        yield None
        return

    try:
        yield state
        await ack_done(interaction)
        # Default to update_status; callers can override by reposting themselves
    except Exception as e:
        await ack_err(interaction, str(e))
        raise


class DMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _require_dm(self, interaction: discord.Interaction, allow_on_hold: bool = False):
        """Return session if invoking user is the DM, else send ephemeral error."""
        state = await require_session(interaction)
        if state is None:
            return None
        if state.dm_user_id != str(interaction.user.id):
            await ack_err(interaction, "Only the DM can use this command.")
            return None
        if not state.session_active and not allow_on_hold:
            await ack_err(interaction, "Session is on hold. Use Resume first.")
            return None
        return state

    # ------------------------------------------------------------------
    # /dm_newsession
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_newsession",
        description="[DM] Create a new session in this channel (pre-start lobby).",
    )
    @app_commands.describe(
        header="Introduction text shown to players above the lobby status block."
    )
    async def dm_newsession(self, interaction: discord.Interaction, header: str = ""):
        await ack(interaction)
        channel_id = str(interaction.channel_id)
        from store import create_session, has_session
        if has_session(channel_id):
            await ack_err(interaction, "A session already exists in this channel.")
            return
        state = create_session(channel_id, dm_user_id=str(interaction.user.id))
        if header:
            intro_msg = await interaction.channel.send(header)
            with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                await intro_msg.pin()
        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_strife
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_strife",
        description="[DM] Toggle combat rounds mode.",
    )
    async def dm_strife(self, interaction: discord.Interaction):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return

            if state.mode == SessionMode.ROUNDS:
                result = exit_rounds(state)
                label = "Combat ended — returning to exploration."
            else:
                result = enter_rounds(state)
                label = "Combat begins!"

            if not result.ok:
                raise ValueError(result.error)

            # Open a fresh turn in the new mode
            if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
                open_turn(state)

            await repost_status(interaction.channel, state, narrative=label)


    # ------------------------------------------------------------------
    # /dm_say
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_say",
        description="[DM] Have an NPC or narrator say something.",
    )
    @app_commands.describe(
        speaker="Speaker name (e.g. 'Goblin King', 'Narrator')",
        text="What they say",
    )
    async def dm_say(self, interaction: discord.Interaction, speaker: str, text: str):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return
            say(state, speaker, text)
            await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_emote
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_emote",
        description="[DM] Describe an NPC or environmental action.",
    )
    @app_commands.describe(
        speaker="Who is acting (e.g. 'Goblin A', 'Narrator')",
        text="What they do (e.g. 'twirls a dagger menacingly')",
    )
    async def dm_emote(self, interaction: discord.Interaction, speaker: str, text: str):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return
            emote(state, speaker, text)
            await update_status(interaction.channel, state)


async def setup(bot: commands.Bot):
    await bot.add_cog(DMCog(bot))
