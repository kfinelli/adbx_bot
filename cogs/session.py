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
    exit_rounds,
    open_turn,
    say,
    submit_turn,
)
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

    @app_commands.command(name="character", description="Show your character sheet.")
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

        a = char.ability_scores
        leader_note = " (Party Leader)" if (
            state.party and state.party.leader_id == char.character_id
        ) else ""
        sep = "\u2500" * 32

        inv_lines = "\n".join(
            f"  {i.quantity}x {i.name}" for i in char.inventory
        ) if char.inventory else "  (empty)"

        st = char.saving_throws
        sheet_lines = [
            sep,
            f"{char.name}  \u2014  {char.character_class.value} Level {char.level}{leader_note}",
            f"HP: {char.hp_current}/{char.hp_max}   AC: {char.armor_class}   Move: {char.movement_speed}'",
            f"XP: {char.experience}   Gold: {char.gold} gp",
            sep,
            f"STR {a.strength:2d}   INT {a.intelligence:2d}",
            f"DEX {a.dexterity:2d}   WIS {a.wisdom:2d}",
            f"CON {a.constitution:2d}   CHA {a.charisma:2d}",
            sep,
            "Saves:",
            "  Death/Poison:    {}".format(st.get("death_poison", "?")),
            "  Wands:           {}".format(st.get("wands", "?")),
            "  Paralysis/Stone: {}".format(st.get("paralysis_stone", "?")),
            "  Breath Weapon:   {}".format(st.get("breath_weapon", "?")),
            "  Spells:          {}".format(st.get("spells", "?")),
            sep,
            "Inventory:",
            inv_lines,
            sep,
        ]
        if char.status_notes:
            sheet_lines.append(f"Status: {char.status_notes}")

        await interaction.edit_original_response(
            content="```\n{}\n```".format("\n".join(sheet_lines))
        )

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
