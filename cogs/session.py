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

from engine import abscond, ask_oracle, enter_rounds, exit_rounds, open_turn, say, submit_turn
from models import SessionMode, TurnStatus
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

        if state.mode == SessionMode.ROUNDS:
            await interaction.followup.send(
                "Combat is active — use /round to submit your action.",
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
        name="round",
        description="Submit your action for the current combat round.",
    )
    @app_commands.describe(action="What does your character do this round?")
    async def round_cmd(self, interaction: discord.Interaction, action: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send(
                "No active session in this channel.", ephemeral=True
            )
            return

        if state.mode != SessionMode.ROUNDS:
            await interaction.followup.send(
                "No combat active — use /turn to submit your action.",
                ephemeral=True,
            )
            return

        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.followup.send(
                "You don't have a character in this session.", ephemeral=True
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
        name="strife",
        description="Toggle combat rounds mode (DM or party leader only).",
    )
    async def strife(self, interaction: discord.Interaction):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send(
                "No active session in this channel.", ephemeral=True
            )
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
            await interaction.followup.send(
                "Only the DM or party leader can toggle combat rounds.",
                ephemeral=True,
            )
            return

        if state.mode == SessionMode.ROUNDS:
            result = exit_rounds(state)
            label = "Combat ended — returning to exploration."
        else:
            result = enter_rounds(state)
            label = "Combat begins!"

        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

        # Open a fresh turn in the new mode
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            open_turn(state)

        await repost_status(interaction.channel, state, narrative=label)

    @app_commands.command(
        name="say",
        description="Your character says something aloud.",
    )
    @app_commands.describe(text="What does your character say?")
    async def say_cmd(self, interaction: discord.Interaction, text: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send("No active session.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.followup.send(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        say(state, char.name, text)
        await update_status(interaction.channel, state)

    @app_commands.command(
        name="oracle",
        description="Ask the DM a question out of character.",
    )
    @app_commands.describe(question="Your question for the DM")
    async def oracle_cmd(self, interaction: discord.Interaction, question: str):
        await ack(interaction)
        state = get_session(str(interaction.channel_id))
        if state is None:
            await interaction.followup.send("No active session.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        asker = char.name if char else interaction.user.display_name
        result, oracle = ask_oracle(state, asker, question)
        if not result.ok:
            await interaction.followup.send(f"\u26a0 {result.error}", ephemeral=True)
            return
        num = oracle.number
        name = oracle.asker_name
        q = oracle.question
        oracle_text = f'**Oracle #{num}** \u2014 {name} asks: "{q}"'
        msg = await interaction.channel.send(oracle_text)
        oracle.message_id = msg.id
        from store import save_session
        save_session(state)

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
