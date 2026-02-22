"""
cogs/session.py — In-game player slash commands.

/arrive is in cogs/arrive.py (handles the DM conversation flow).

Commands here are only valid during active gameplay (EXPLORATION or ROUNDS):
  /turn    — submit your action for the current turn
  /abscond — party leader moves group through a numbered exit
  /status  — repost the status block
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from engine import abscond, open_turn, submit_turn
from store import (
    ack,
    get_session,
    notify_dm_of_turn_close,
    repost_status,
    update_status,
)


class SessionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="turn",
        description="Describe what your character does this turn.",
    )
    @app_commands.describe(action="What does your character do?")
    async def turn(self, interaction: discord.Interaction, action: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send(
                "No active session in this channel.", ephemeral=True
            )
            return

        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.followup.send(
                "You don't have a character in this session. Use /arrive first.",
                ephemeral=True,
            )
            return

        turn_number = state.turn_number
        result = submit_turn(state, char.character_id, action)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

        await update_status(interaction.channel, state)

        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    @app_commands.command(
        name="abscond",
        description="[Party leader] Move the party through a numbered exit.",
    )
    @app_commands.describe(exit_number="The number of the exit to take (see status block)")
    async def abscond_cmd(self, interaction: discord.Interaction, exit_number: int):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send(
                "No active session in this channel.", ephemeral=True
            )
            return

        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.followup.send(
                "You don't have a character in this session.", ephemeral=True
            )
            return

        turn_number = state.turn_number
        result = abscond(state, char.character_id, exit_number)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

        await update_status(interaction.channel, state)

        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    @app_commands.command(
        name="status",
        description="Repost the current game status at the bottom of the channel.",
    )
    async def status(self, interaction: discord.Interaction):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send(
                "No active session in this channel.", ephemeral=True
            )
            return
        await repost_status(interaction.channel, state)


def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))
