"""
cogs/session.py — Player-facing slash commands.

Commands:
  /embark   — create a character and join the session
  /turn     — submit your action for the current turn
  /status   — reprint the status block
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from models import CharacterClass
from engine import (
    create_character,
    open_turn,
    submit_turn,
)
from store import (
    create_session,
    get_session,
    has_session,
    post_or_update_status,
    require_session,
    ok,
    err,
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

    # ------------------------------------------------------------------
    # /embark
    # ------------------------------------------------------------------

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
        channel_id = str(interaction.channel_id)

        # Create a session if one doesn't exist yet
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
            await err(interaction, result.error)
            return

        # Open first turn automatically if none exists
        if state.current_turn is None:
            open_turn(state)

        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /turn
    # ------------------------------------------------------------------

    @app_commands.command(
        name="turn",
        description="Describe what your character does this turn.",
    )
    @app_commands.describe(action="What does your character do?")
    async def turn(self, interaction: discord.Interaction, action: str):
        state = await require_session(interaction)
        if state is None:
            return

        # Find the character owned by this user
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await err(interaction, "You don't have a character in this session. Use /embark first.")
            return

        result = submit_turn(state, char.character_id, action)
        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, f"✓ Turn submitted for **{char.name}**: \"{action}\"")
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------

    @app_commands.command(
        name="status",
        description="Reprint the current game status.",
    )
    async def status(self, interaction: discord.Interaction):
        state = await require_session(interaction)
        if state is None:
            return
        await interaction.response.defer()
        await post_or_update_status(interaction.channel, state)
        await interaction.followup.send("Status updated.", ephemeral=True)


def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCog(bot))
