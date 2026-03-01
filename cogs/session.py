"""
cogs/session.py — In-game player slash commands.

/arrive is in cogs/arrive.py (handles the DM conversation flow).

Commands:
  /help      — instructions for new players (ephemeral)
  /character — show your character sheet (ephemeral)
  /turn      — submit action for exploration turn
  /round     — submit action for combat round
  /abscond   — party leader moves group through a numbered exit
  /say       — character speaks aloud (added to status say log)
  /oracle    — ask DM a question (posted as persistent channel message)
  /strife    — toggle combat rounds (DM or party leader)
  /status    — repost status block
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

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
    get_session,
    notify_dm_of_turn_close,
    repost_status,
    save_session,
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
        await interaction.followup.send(HELP_TEXT, ephemeral=True)

    # ------------------------------------------------------------------
    # /character
    # ------------------------------------------------------------------

    @app_commands.command(name="character", description="Show your character sheet.")
    async def character_cmd(self, interaction: discord.Interaction):
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

        a = char.ability_scores
        leader_note = " (Party Leader)" if (
            state.party and state.party.leader_id == char.character_id
        ) else ""
        sep = "\u2500" * 32

        inv_lines = "\n".join(
            "  {}x {}".format(i.quantity, i.name) for i in char.inventory
        ) if char.inventory else "  (empty)"

        st = char.saving_throws
        sheet_lines = [
            sep,
            "{name}  \u2014  {cls} Level {lvl}{leader}".format(
                name=char.name,
                cls=char.character_class.value,
                lvl=char.level,
                leader=leader_note,
            ),
            "HP: {cur}/{mx}   AC: {ac}   Move: {mv}'".format(
                cur=char.hp_current, mx=char.hp_max,
                ac=char.armor_class, mv=char.movement_speed,
            ),
            "XP: {}   Gold: {} gp".format(char.experience, char.gold),
            sep,
            "STR {:2d}   INT {:2d}".format(a.strength, a.intelligence),
            "DEX {:2d}   WIS {:2d}".format(a.dexterity, a.wisdom),
            "CON {:2d}   CHA {:2d}".format(a.constitution, a.charisma),
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
            sheet_lines.append("Status: {}".format(char.status_notes))

        await interaction.followup.send(
            "```\n{}\n```".format("\n".join(sheet_lines)),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /turn
    # ------------------------------------------------------------------

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
                "Combat is active \u2014 use /round to submit your action.",
                ephemeral=True,
            )
            return

        turn_number = state.turn_number
        result = submit_turn(state, char.character_id, action)
        if not result.ok:
            await interaction.followup.send(
                "\u26a0 {}".format(result.error), ephemeral=True
            )
            return

        await update_status(interaction.channel, state)

        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    # ------------------------------------------------------------------
    # /round
    # ------------------------------------------------------------------

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
                "No combat active \u2014 use /turn to submit your action.",
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
            await interaction.followup.send(
                "\u26a0 {}".format(result.error), ephemeral=True
            )
            return

        await update_status(interaction.channel, state)

        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    # ------------------------------------------------------------------
    # /abscond
    # ------------------------------------------------------------------

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
            await interaction.followup.send(
                "\u26a0 {}".format(result.error), ephemeral=True
            )
            return

        await update_status(interaction.channel, state)

        if result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)

    # ------------------------------------------------------------------
    # /say
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # /emote
    # ------------------------------------------------------------------

    @app_commands.command(
        name="emote",
        description="Describe your character doing something.",
    )
    @app_commands.describe(text="What does your character do? (e.g. 'nods respectfully')")
    async def emote_cmd(self, interaction: discord.Interaction, text: str):
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
        emote(state, char.name, text)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /oracle
    # ------------------------------------------------------------------

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
        result, oracle = ask_oracle(
            state, asker, question, asker_owner_id=str(interaction.user.id)
        )
        if not result.ok:
            await interaction.followup.send(
                "\u26a0 {}".format(result.error), ephemeral=True
            )
            return
        oracle_text = (
            "**Oracle #{}** \u2014 {} asks: \"{}\"".format(
                oracle.number, oracle.asker_name, oracle.question
            )
        )
        msg = await interaction.channel.send(oracle_text)
        oracle.message_id = msg.id
        save_session(state)

    # ------------------------------------------------------------------
    # /strife
    # ------------------------------------------------------------------

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
            label = "Combat ended \u2014 returning to exploration."
        else:
            result = enter_rounds(state)
            label = "Combat begins!"

        if not result.ok:
            await interaction.followup.send(
                "\u26a0 {}".format(result.error), ephemeral=True
            )
            return

        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            open_turn(state)

        await repost_status(interaction.channel, state, narrative=label)

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------

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
