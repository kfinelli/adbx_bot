"""
cogs/session.py — Player-facing slash commands.

Commands:
  /embark   — create a character and join the session
  /turn     — submit your action for the current turn
  /status   — repost the status block at the bottom of the channel
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from models import CharacterClass
from engine import abscond, create_character, open_turn, submit_turn
from store import (
    ack, err,
    create_session, get_session, has_session,
    notify_dm_of_turn_close,
    repost_status, update_status,
    require_session,
)
from tables import EQUIPMENT_PACKAGES


CLASS_CHOICES = [
    app_commands.Choice(name=cls.value, value=cls.name)
    for cls in CharacterClass
]

PACKAGE_CHOICES = [
    app_commands.Choice(name=name, value=name)
    for name in EQUIPMENT_PACKAGES
]


class SessionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="embark",
        description="Create a character and join the dungeon session.",
    )
    @app_commands.describe(
        name="Your character's name",
        character_class="Choose a class",
        equipment_package="Choose a starting equipment package",
    )
    @app_commands.choices(
        character_class=CLASS_CHOICES,
        equipment_package=PACKAGE_CHOICES,
    )
    async def embark(
        self,
        interaction: discord.Interaction,
        name: str,
        character_class: app_commands.Choice[str],
        equipment_package: app_commands.Choice[str],
    ):
        await ack(interaction)
        channel_id = str(interaction.channel_id)

        if not has_session(channel_id):
            state = create_session(channel_id, dm_user_id=str(interaction.user.id))
        else:
            state = get_session(channel_id)

        cls = CharacterClass[character_class.value]
        result = create_character(
            state=state,
            name=name,
            character_class=cls,
            equipment_package=equipment_package.value,
            owner_id=str(interaction.user.id),
        )

        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

        if state.party.leader_id is None:
            state.party.leader_id = state.party.member_ids[-1]

        if state.current_turn is None:
            open_turn(state)

        await update_status(interaction.channel, state)

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
                "You don't have a character in this session. Use /embark first.",
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


    @app_commands.command(
        name="abscond",
        description="[Party leader] Move the party through a numbered exit.",
    )
    @app_commands.describe(exit_number="The number of the exit to take (see status block)")
    async def abscond_cmd(self, interaction: discord.Interaction, exit_number: int):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send("No active session in this channel.", ephemeral=True)
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


def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))
