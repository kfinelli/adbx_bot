"""
cogs/arrive.py — /arrive command for player character creation.

Flow:
  1. Player uses /arrive in the game channel
  2. Bot sends them a DM with their rolled stats and Accept/Reroll buttons
  3. On accept, bot sends class selection buttons
  4. On class select, bot sends loadout selection buttons
  5. On loadout select, character is created and channel status updates

/arrive is only valid during PRE_START mode.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from models import CharacterClass, SessionMode
from engine import create_character, open_turn, roll_stats
from store import ack, get_session, save_session, update_status
from tables import EQUIPMENT_PACKAGES, EQUIPMENT_PACKAGE_DESCRIPTIONS


# ---------------------------------------------------------------------------
# Stat display helper
# ---------------------------------------------------------------------------

def _fmt_stats(stats: dict) -> str:
    """Format rolled stats for display in a DM message."""
    lines = [
        f"**STR** {stats['strength']:2d}   **INT** {stats['intelligence']:2d}",
        f"**DEX** {stats['dexterity']:2d}   **WIS** {stats['wisdom']:2d}",
        f"**CON** {stats['constitution']:2d}   **CHA** {stats['charisma']:2d}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3: Loadout selection view
# ---------------------------------------------------------------------------

class LoadoutView(discord.ui.View):
    def __init__(
        self,
        channel_id: str,
        character_name: str,
        character_class: CharacterClass,
        stats: dict,
        owner_id: str,
    ):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.character_name = character_name
        self.character_class = character_class
        self.stats = stats
        self.owner_id = owner_id

        for package_name in EQUIPMENT_PACKAGES:
            desc = EQUIPMENT_PACKAGE_DESCRIPTIONS.get(package_name, "")
            btn = discord.ui.Button(
                label=package_name,
                style=discord.ButtonStyle.secondary,
                custom_id=f"loadout_{package_name}",
            )
            btn.callback = self._make_callback(package_name)
            self.add_item(btn)
        self._pack_list = "\n".join(
            f"**{name}:** {desc}"
            for name, desc in EQUIPMENT_PACKAGE_DESCRIPTIONS.items()
        )

    def _pack_summary(self) -> str:
        return self._pack_list

    def _make_callback(self, package_name: str):
        async def callback(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Loadout chosen: **{package_name}**. Creating character...",
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
                    character_class=self.character_class,
                    equipment_package=package_name,
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

                # Save unconditionally — don't rely on get_channel succeeding.
                save_session(state)

                await interaction.followup.send(
                    f"✓ **{self.character_name}** has arrived! "
                    f"Head back to the game channel.",
                    ephemeral=False,
                )

                # Best-effort channel status update (get_channel may miss uncached guilds).
                channel = interaction.client.get_channel(int(self.channel_id))
                if channel is None:
                    # Fall back to a fetch if not in cache
                    try:
                        channel = await interaction.client.fetch_channel(int(self.channel_id))
                    except discord.HTTPException:
                        channel = None
                if channel is not None:
                    await update_status(channel, state)

            except Exception as exc:
                # Surface any unexpected error to the player rather than silently failing.
                try:
                    await interaction.followup.send(
                        f"⚠ Something went wrong: {exc}", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
                raise  # re-raise so the traceback still hits bot stderr

        return callback


# ---------------------------------------------------------------------------
# Step 2: Class selection view
# ---------------------------------------------------------------------------

class ClassView(discord.ui.View):
    def __init__(self, channel_id: str, character_name: str, stats: dict, owner_id: str):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.character_name = character_name
        self.stats = stats
        self.owner_id = owner_id

        for cls in CharacterClass:
            btn = discord.ui.Button(
                label=cls.value,
                style=discord.ButtonStyle.primary,
                custom_id=f"class_{cls.name}",
            )
            btn.callback = self._make_callback(cls)
            self.add_item(btn)

    def _make_callback(self, character_class: CharacterClass):
        async def callback(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=f"Class chosen: **{character_class.value}**. Now pick your starting loadout:",
                view=self,
            )
            loadout_view = LoadoutView(
                channel_id=self.channel_id,
                character_name=self.character_name,
                character_class=character_class,
                stats=self.stats,
                owner_id=self.owner_id,
            )
            pack_info = loadout_view._pack_summary()
            await interaction.followup.send(
                "Choose your starting equipment pack:\n\n" + pack_info,
                view=loadout_view,
            )

        return callback


# ---------------------------------------------------------------------------
# Step 1: Stat roll view (Accept / Reroll)
# ---------------------------------------------------------------------------

class StatRollView(discord.ui.View):
    def __init__(self, channel_id: str, character_name: str, owner_id: str):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.character_name = character_name
        self.owner_id = owner_id
        self.stats = roll_stats()

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
            content=f"Stats accepted!\n\n{_fmt_stats(self.stats)}\n\nNow choose your class:",
            view=self,
        )
        class_view = ClassView(
            channel_id=self.channel_id,
            character_name=self.character_name,
            stats=self.stats,
            owner_id=self.owner_id,
        )
        await interaction.followup.send("Choose your class:", view=class_view)

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
    @app_commands.describe(name="Your character's name")
    async def arrive(self, interaction: discord.Interaction, name: str):
        await ack(interaction)
        channel_id = str(interaction.channel_id)

        state = get_session(channel_id)
        if state is None:
            await interaction.followup.send(
                "No session in this channel. Ask the DM to create one.",
                ephemeral=True,
            )
            return

        if state.mode != SessionMode.PRE_START:
            await interaction.followup.send(
                "The session has already started. New characters cannot join mid-session.",
                ephemeral=True,
            )
            return

        # Check player doesn't already have a character
        owner_id = str(interaction.user.id)
        for char in state.characters.values():
            if char.owner_id == owner_id:
                await interaction.followup.send(
                    f"You already have a character in this session: **{char.name}**.",
                    ephemeral=True,
                )
                return

        # Try to DM the player
        try:
            dm_channel = await interaction.user.create_dm()
            view = StatRollView(
                channel_id=channel_id,
                character_name=name,
                owner_id=owner_id,
            )
            await dm_channel.send(view._stats_message(), view=view)
            await interaction.followup.send(
                "Check your DMs to roll stats and choose your class!",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I couldn't DM you. Please enable DMs from server members and try again.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ArriveCog(bot))
