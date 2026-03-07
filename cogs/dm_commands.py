"""
cogs/dm_commands.py — DM-facing slash commands.

All responses are ephemeral (errors only) or silent.
The channel stays clean — feedback comes from the status block updating.

Commands:
  /dm_resolve     — write turn resolution narrative, advance turn
  /dm_sethp       — set HP for a character or NPC (by name)
  /dm_setstatus   — set a character's status and notes
  /dm_setroom     — set the current room name and description
  /dm_addfeature  — add an interactive feature to the current room
  /dm_setfeature  — update a feature's state string
  /dm_addnpc      — add an NPC to the current room
  /dm_setnpcstatus — update an NPC's status (e.g. dead, fled)
  /dm_setlight    — set the active light source
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from models import (
    CharacterStatus,
    DoorState,
    Exit,
    NPC,
    Room,
    RoomFeature,
)
from engine import (
    add_exit,
    add_npc,
    answer_oracle,
    close_turn,
    emote,
    enter_rounds,
    exit_rounds,
    hold_session,
    open_turn,
    resolve_turn,
    resume_session,
    say,
    set_character_hp,
    set_character_status,
    set_exit_state,
    set_feature_state,
    set_light_source,
    set_npc_hp,
    set_npc_status,
    set_room,
    start_session,
)
from models import SessionMode, TurnStatus
from discord_tasks import dispatch_oracle_answer, dispatch_turn_resolved
from store import (
    ack,
    ack_done,
    ack_err,
    delete_session,
    get_session,
    update_status,
    require_session,
)


class DMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _require_dm(self, interaction: discord.Interaction, allow_on_hold: bool = False):
        """Return session if invoking user is the DM, else send ephemeral error."""
        state = await require_session(interaction)
        if state is None:
            return None
        if state.dm_user_id != str(interaction.user.id):
            await err(interaction, "Only the DM can use this command.")
            return None
        if not state.session_active and not allow_on_hold:
            await err(interaction, "Session is on hold. Use /dm_resume first.")
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

        delete_session(channel_id)
        await interaction.channel.send(
            "Session ended. Use `/dm_newsession` to start a new lobby."
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
        from store import has_session, create_session
        if has_session(channel_id):
            await ack_err(interaction, "A session already exists in this channel.")
            return
        state = create_session(channel_id, dm_user_id=str(interaction.user.id))
        if header:
            intro_msg = await interaction.channel.send(header)
            try:
                await intro_msg.pin()
            except (discord.Forbidden, discord.HTTPException):
                pass  # pin is best-effort
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
        state = await self._require_dm(interaction, allow_on_hold=True)
        if state is None:
            return
        result = start_session(state)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return

        if state.mode == SessionMode.ROUNDS:
            result = exit_rounds(state)
            label = "Combat ended — returning to exploration."
        else:
            result = enter_rounds(state)
            label = "Combat begins!"

        if not result.ok:
            await ack_err(interaction, result.error)
            return

        # Open a fresh turn in the new mode
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            open_turn(state)

        await ack_done(interaction)
        await repost_status(interaction.channel, state, narrative=label)

    # ------------------------------------------------------------------
    # /dm_resolve
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_resolve",
        description="[DM] Resolve the current turn with a narrative and advance.",
    )
    @app_commands.describe(narrative="What happens as a result of the players' actions.")
    async def dm_resolve(self, interaction: discord.Interaction, narrative: str):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        if state.current_turn is None:
            await ack_err(interaction, "No open turn to resolve.")
            return

        close_turn(state)
        result = resolve_turn(state, narrative)
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        open_turn(state)
        # Post narrative visibly, then fresh status below it
        await ack_done(interaction)
        await dispatch_turn_resolved(interaction.channel, state, narrative)

    # ------------------------------------------------------------------
    # /dm_sethp
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_sethp",
        description="[DM] Set HP for a character or NPC by name.",
    )
    @app_commands.describe(target_name="Name of the character or NPC", hp="New HP value")
    async def dm_sethp(self, interaction: discord.Interaction, target_name: str, hp: int):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        char = _find_char_by_name(state, target_name)
        if char is not None:
            result = set_character_hp(state, char.character_id, hp)
        else:
            npc = _find_npc_by_name(state, target_name)
            if npc is None:
                await ack_err(interaction, "No character or NPC named '{}'".format(target_name))
                return
            result = set_npc_hp(state, npc.npc_id, hp)

        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setstatus
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setstatus",
        description="[DM] Set a character's status and optional notes.",
    )
    @app_commands.describe(
        character_name="Character name",
        status="Status: active, dead, fled, petrified, paralyzed",
        notes="Short note shown in status block (e.g. 'fatigued', 'poisoned')",
    )
    async def dm_setstatus(
        self,
        interaction: discord.Interaction,
        character_name: str,
        status: str,
        notes: str = "",
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        char = _find_char_by_name(state, character_name)
        if char is None:
            await ack_err(interaction, "No character named '{}'".format(character_name))
            return

        try:
            char_status = CharacterStatus(status.lower())
        except ValueError:
            valid = [s.value for s in CharacterStatus]
            await ack_err(interaction, "Unknown status '{}'. Valid: {}".format(status, valid))
            return

        result = set_character_status(state, char.character_id, char_status, notes)
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setroom
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setroom",
        description="[DM] Move the party to a new room.",
    )
    @app_commands.describe(
        name="Room name (e.g. 'Blue Atrium')",
        description="Player-visible room description",
        notes="DM-facing notes (not shown to players)",
    )
    async def dm_setroom(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str,
        notes: str = "",
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = Room(name=name, description=description, notes=notes)
        set_room(state, room)
        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_addfeature
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_addfeature",
        description="[DM] Add an interactive feature to the current room.",
    )
    @app_commands.describe(
        name="Feature name (e.g. 'Brass chandelier')",
        description="Player-visible description",
        state_str="Initial state (default: intact)",
    )
    async def dm_addfeature(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str,
        state_str: str = "intact",
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = state.current_room
        if room is None:
            await ack_err(interaction, "No current room. Use /dm_setroom first.")
            return

        room.features.append(RoomFeature(name=name, description=description, state=state_str))
        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setfeature
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setfeature",
        description="[DM] Update the state of a room feature.",
    )
    @app_commands.describe(
        feature_name="Name of the feature to update",
        new_state="New state (e.g. 'smashed', 'unlocked')",
    )
    async def dm_setfeature(
        self,
        interaction: discord.Interaction,
        feature_name: str,
        new_state: str,
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = state.current_room
        if room is None:
            await ack_err(interaction, "No current room.")
            return

        feature = next(
            (f for f in room.features if f.name.lower() == feature_name.lower()), None
        )
        if feature is None:
            await ack_err(interaction, "No feature named '{}' in this room.".format(feature_name))
            return

        result = set_feature_state(state, feature.feature_id, new_state)
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_addnpc
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_addnpc",
        description="[DM] Add an NPC or monster to the current room.",
    )
    @app_commands.describe(
        name="NPC name (e.g. 'Goblin A')",
        hp="Hit points",
        ac="Armor class (descending AC, e.g. 6)",
        description="Brief description shown in status block",
        damage_dice="Damage dice string (e.g. 1d6)",
        notes="DM-facing notes (not shown to players)",
    )
    async def dm_addnpc(
        self,
        interaction: discord.Interaction,
        name: str,
        hp: int,
        ac: int = 9,
        description: str = "",
        damage_dice: str = "1d6",
        notes: str = "",
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        npc = NPC(
            name=name,
            hp_max=hp,
            hp_current=hp,
            armor_class=ac,
            description=description,
            damage_dice=damage_dice,
            notes=notes,
        )
        add_npc(state, npc)
        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setnpcstatus
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setnpcstatus",
        description="[DM] Update an NPC's status (e.g. dead, fled, charmed).",
    )
    @app_commands.describe(npc_name="Name of the NPC", status="New status string")
    async def dm_setnpcstatus(
        self,
        interaction: discord.Interaction,
        npc_name: str,
        status: str,
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        npc = _find_npc_by_name(state, npc_name)
        if npc is None:
            await ack_err(interaction, "No NPC named '{}'".format(npc_name))
            return

        result = set_npc_status(state, npc.npc_id, status.lower())
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
        await update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setlight
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setlight",
        description="[DM] Set the active light source.",
    )
    @app_commands.describe(
        label="Description of the light source (e.g. 'Torch', 'Lantern')",
        turns="Remaining turns (omit or set -1 for permanent/magical)",
    )
    async def dm_setlight(
        self,
        interaction: discord.Interaction,
        label: str,
        turns: int = -1,
    ):
        await ack(interaction)
        state = await self._require_dm(interaction)
        if state is None:
            return

        turns_remaining = None if turns < 0 else turns
        result = set_light_source(state, label, turns_remaining)
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
        await update_status(interaction.channel, state)


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
        state = await self._require_dm(interaction)
        if state is None:
            return

        try:
            ds = DoorState(door_state.lower())
        except ValueError:
            valid = [d.value for d in DoorState]
            await ack_err(interaction, "Unknown door state '{}'. Valid: {}".format(door_state, valid))
            return

        result = add_exit(state, label, description, ds, notes)
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = state.current_room
        if room is None:
            await ack_err(interaction, "No current room.")
            return

        idx = exit_number - 1
        if idx < 0 or idx >= len(room.exits):
            await ack_err(interaction, "Exit {} does not exist.".format(exit_number))
            return

        try:
            ds = DoorState(door_state.lower())
        except ValueError:
            valid = [d.value for d in DoorState]
            await ack_err(interaction, "Unknown door state '{}'. Valid: {}".format(door_state, valid))
            return

        result = set_exit_state(state, room.exits[idx].exit_id, ds)
        if not result.ok:
            await ack_err(interaction, result.error)
            return

        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return

        if hours <= 0:
            await ack_err(interaction, "Turn length must be greater than 0.")
            return

        state.default_turn_hours = hours
        from store import save_session
        save_session(state)
        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return

        if state.current_turn is None:
            await ack_err(interaction, "No open turn.")
            return

        if hours <= 0:
            await ack_err(interaction, "Duration must be greater than 0.")
            return

        from datetime import datetime, timedelta, timezone
        state.current_turn.due_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        from store import save_session
        save_session(state)
        await ack_done(interaction)
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
        state = await self._require_dm(interaction, allow_on_hold=True)
        if state is None:
            return
        result = hold_session(state)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await ack_done(interaction)
        await repost_status(interaction.channel, state)

    @app_commands.command(
        name="dm_resume",
        description="[DM] Resume a session that is on hold.",
    )
    async def dm_resume(self, interaction: discord.Interaction):
        await ack(interaction)
        state = await self._require_dm(interaction, allow_on_hold=True)
        if state is None:
            return
        result = resume_session(state)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return
        say(state, speaker, text)
        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return
        emote(state, speaker, text)
        await ack_done(interaction)
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
        state = await self._require_dm(interaction)
        if state is None:
            return
        result, oracle = answer_oracle(state, number, answer)
        if not result.ok:
            await ack_err(interaction, result.error)
            return
        await dispatch_oracle_answer(self.bot, interaction.channel, oracle)

        from store import save_session
        save_session(state)
        await ack_done(interaction)

# Lookup helpers
# ---------------------------------------------------------------------------

def _find_char_by_name(state, name: str):
    for char in state.characters.values():
        if char.name.lower() == name.lower():
            return char
    return None


def _find_npc_by_name(state, name: str):
    for npc in state.npcs:
        if npc.name.lower() == name.lower():
            return npc
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(DMCog(bot))
