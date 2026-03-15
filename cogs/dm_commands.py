"""
cogs/dm_commands.py — DM-facing slash commands.

All responses are ephemeral (errors only) or silent.
The channel stays clean — feedback comes from the status block updating.

Commands:
  /dm_reset       — end the session permanently
  /dm_newsession  — create a new session in this channel
  /embark         — begin the session (move from lobby to dungeon)
  /dm_strife      — toggle combat rounds mode
  /dm_addexit     — add an exit to the current room
  /dm_setexitstate — set door state on an exit
  /dm_setturnlength — set default turn length
  /dm_settimer    — override current turn timer
  /dm_hold        — put session on hold
  /dm_resume      — resume a held session
  /dm_say         — post a message as a speaker
  /dm_emote       — post an emote message
  /dm_oracle      — answer an oracle question

Note: Many former /dm_* commands have been removed as their functionality
is now available in the WebUI: /dm_resolve, /dm_sethp, /dm_setstatus,
/dm_setroom, /dm_addfeature, /dm_setfeature, /dm_addnpc, /dm_setnpcstatus,
/dm_setlight.
"""

from __future__ import annotations

import contextlib
from datetime import UTC

import discord
from discord import app_commands
from discord.ext import commands

from discord_tasks import dispatch_oracle_answer, dispatch_turn_resolved
from engine import (
    add_exit,
    answer_oracle,
    close_turn,
    emote,
    enter_rounds,
    exit_rounds,
    hold_session,
    move_party_to_room,
    open_turn,
    resolve_turn,
    resume_session,
    say,
    set_exit_state,
    set_feature_state,
    set_light_source,
    start_session,
)
from models import (
    DoorState,
    Room,
    RoomFeature,
    SessionMode,
    TurnStatus,
)
from store import (
    ack,
    ack_done,
    ack_err,
    archive_session,
    get_session,
    repost_status,
    require_session,
    save_session_async,
    update_status,
)
from validation import (
    validate_turn_hours,
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
            await ack_err(interaction, "Session is on hold. Use /dm_resume first.")
            return None
        return state

    # ------------------------------------------------------------------
    # /dm_reset
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_reset",
        description="[DM] End the session permanently. Use /dm_newsession to start a new one.",
    )
    async def dm_reset(self, interaction: discord.Interaction):
        await ack(interaction)
        channel_id = str(interaction.channel_id)

        state = get_session(channel_id)
        if state is None:
            await ack_err(interaction, "No active session in this channel.")
            return

        if state.dm_user_id and state.dm_user_id != str(interaction.user.id):
            await ack_err(interaction, "Only the DM who created this session can reset it.")
            return

        channel_name = interaction.channel.name if hasattr(interaction.channel, "name") else ""
        archived = await archive_session(channel_id, channel_name)
        note = "archived" if archived else "cleared"
        await interaction.channel.send(
            f"Session {note}. Use `/dm_newsession` to start a new lobby."
        )

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
    # /embark
    # ------------------------------------------------------------------

    @app_commands.command(
        name="embark",
        description="[DM] Begin the session — move from lobby into the dungeon.",
    )
    async def embark(self, interaction: discord.Interaction):
        await ack(interaction)
        async with dm_command_context(interaction, self, allow_on_hold=True) as state:
            if state is None:
                return
            result = start_session(state)
            if not result.ok:
                raise ValueError(result.error)
            await repost_status(interaction.channel, state)

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
    # /dm_addexit
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_addexit",
        description="[DM] Add an exit to the current room.",
    )
    @app_commands.describe(
        label="Exit label (e.g. 'north', 'west door', 'ladder down')",
        description="Player-visible description of the exit",
        door_state="Door state: open, closed, locked, stuck, secret",
        notes="DM-facing notes (not shown to players)",
    )
    async def dm_addexit(
        self,
        interaction: discord.Interaction,
        label: str,
        description: str,
        door_state: str = "open",
        notes: str = "",
    ):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return

            try:
                ds = DoorState(door_state.lower())
            except ValueError as valerr:
                valid = [d.value for d in DoorState]
                raise ValueError(f"Unknown door state '{door_state}'. Valid: {valid}") from valerr

            result = add_exit(state, label, description, ds, notes)
            if not result.ok:
                raise ValueError(result.error)

            await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setexitstate
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setexitstate",
        description="[DM] Change the state of a numbered exit (e.g. unlock a door).",
    )
    @app_commands.describe(
        exit_number="Exit number as shown in the status block",
        door_state="New state: open, closed, locked, stuck, secret",
    )
    async def dm_setexitstate(
        self,
        interaction: discord.Interaction,
        exit_number: int,
        door_state: str,
    ):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return

            room = state.current_room
            if room is None:
                raise ValueError("No current room.")

            idx = exit_number - 1
            if idx < 0 or idx >= len(room.exits):
                raise ValueError(f"Exit {exit_number} does not exist.")

            try:
                ds = DoorState(door_state.lower())
            except ValueError as valerr:
                valid = [d.value for d in DoorState]
                raise ValueError(f"Unknown door state '{door_state}'. Valid: {valid}") from valerr

            result = set_exit_state(state, room.exits[idx].exit_id, ds)
            if not result.ok:
                raise ValueError(result.error)

            await update_status(interaction.channel, state)


    # ------------------------------------------------------------------
    # /dm_setturnlength
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setturnlength",
        description="[DM] Set the default turn length for this session.",
    )
    @app_commands.describe(hours="Default turn duration in hours (e.g. 24)")
    async def dm_setturnlength(self, interaction: discord.Interaction, hours: float):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return

            # Validate turn hours using shared validator
            hours_result = validate_turn_hours(hours)
            if not hours_result:
                raise ValueError(hours_result.error)

            state.default_turn_hours = hours_result.value
            await save_session_async(state)
            await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_settimer
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_settimer",
        description="[DM] Override the deadline for the current open turn.",
    )
    @app_commands.describe(hours="Hours from now until this turn expires")
    async def dm_settimer(self, interaction: discord.Interaction, hours: float):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return

            if state.current_turn is None:
                raise ValueError("No open turn.")

            # Validate turn hours using shared validator
            hours_result = validate_turn_hours(hours)
            if not hours_result:
                raise ValueError(hours_result.error)

            from datetime import datetime, timedelta
            state.current_turn.due_at = datetime.now(UTC) + timedelta(hours=hours_result.value)
            await save_session_async(state)
            await update_status(interaction.channel, state)


    # ------------------------------------------------------------------
    # /dm_hold and /dm_resume
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_hold",
        description="[DM] Put the session on hold (no turns accepted).",
    )
    async def dm_hold(self, interaction: discord.Interaction):
        await ack(interaction)
        async with dm_command_context(interaction, self, allow_on_hold=True) as state:
            if state is None:
                return
            result = hold_session(state)
            if not result.ok:
                raise ValueError(result.error)
            await repost_status(interaction.channel, state)

    @app_commands.command(
        name="dm_resume",
        description="[DM] Resume a session that is on hold.",
    )
    async def dm_resume(self, interaction: discord.Interaction):
        await ack(interaction)
        async with dm_command_context(interaction, self, allow_on_hold=True) as state:
            if state is None:
                return
            result = resume_session(state)
            if not result.ok:
                raise ValueError(result.error)
            await repost_status(interaction.channel, state)


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

    # ------------------------------------------------------------------
    # /dm_oracle
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_oracle",
        description="[DM] Answer a player oracle by number.",
    )
    @app_commands.describe(
        number="Oracle number to answer",
        answer="Your response",
    )
    async def dm_oracle(self, interaction: discord.Interaction, number: int, answer: str):
        await ack(interaction)
        async with dm_command_context(interaction, self) as state:
            if state is None:
                return
            result = answer_oracle(state, number, answer)
            if not result.ok:
                raise ValueError(result.error)
            oracle = result.data
            await dispatch_oracle_answer(self.bot, interaction.channel, oracle)

            await save_session_async(state)

# Lookup helpers are no longer needed since dm_sethp, dm_setstatus, dm_addnpc, and dm_setnpcstatus
# have been removed (functionality moved to WebUI). These helpers were only used by those commands.


async def setup(bot: commands.Bot):
    await bot.add_cog(DMCog(bot))
