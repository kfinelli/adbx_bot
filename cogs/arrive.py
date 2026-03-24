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

import discord
from discord import app_commands
from discord.ext import commands

from engine import create_character, roll_stats
from engine.azure_constants import POWER_LEVEL
from models import CharacterClass, SessionMode
from store import get_characters_by_owner, get_session, save_session, update_status
from validation import validate_character_name

# ---------------------------------------------------------------------------
# Stat display helper
# ---------------------------------------------------------------------------

def _fmt_stats(stats: dict) -> str:
    """Format rolled Azure stats for display in a DM message."""
    def _fmt(val: int) -> str:
        # Display scaled integer as a decimal, e.g. 250 → "+2.50"
        fval = val / POWER_LEVEL
        return f"{fval:+.2f}"

    return (
        f"**PHY** {_fmt(stats['physique'])}   "
        f"**FNS** {_fmt(stats['finesse'])}\n"
        f"**RSN** {_fmt(stats['reason'])}   "
        f"**SVY** {_fmt(stats['savvy'])}"
    )


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

                save_session(state)

                await interaction.followup.send(
                    f"✓ **{self.character_name}** the {character_class.value} has arrived! "
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
