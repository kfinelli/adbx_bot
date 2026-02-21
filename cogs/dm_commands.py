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
    close_turn,
    open_turn,
    resolve_turn,
    set_character_hp,
    set_character_status,
    set_exit_state,
    set_feature_state,
    set_light_source,
    set_npc_hp,
    set_npc_status,
    set_room,
)
from store import (
    ack, err,
    get_session,
    repost_status,
    update_status,
    require_session,
)


class DMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _require_dm(self, interaction: discord.Interaction):
        """Return session if invoking user is the DM, else send ephemeral error."""
        state = await require_session(interaction)
        if state is None:
            return None
        if state.dm_user_id != str(interaction.user.id):
            await err(interaction, "Only the DM can use this command.")
            return None
        return state

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
            await interaction.followup.send("⚠ No open turn to resolve.", ephemeral=True)
            return

        close_turn(state)
        result = resolve_turn(state, narrative)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

        open_turn(state)
        # Post narrative visibly, then fresh status below it
        await repost_status(interaction.channel, state, narrative=narrative)

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
                await interaction.followup.send(
                    f"⚠ No character or NPC named '{target_name}'.", ephemeral=True
                )
                return
            result = set_npc_hp(state, npc.npc_id, hp)

        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

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
            await interaction.followup.send(
                f"⚠ No character named '{character_name}'.", ephemeral=True
            )
            return

        try:
            char_status = CharacterStatus(status.lower())
        except ValueError:
            valid = [s.value for s in CharacterStatus]
            await interaction.followup.send(
                f"⚠ Unknown status '{status}'. Valid: {valid}", ephemeral=True
            )
            return

        result = set_character_status(state, char.character_id, char_status, notes)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

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
            await interaction.followup.send(
                "⚠ No current room. Use /dm_setroom first.", ephemeral=True
            )
            return

        room.features.append(RoomFeature(name=name, description=description, state=state_str))
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
            await interaction.followup.send("⚠ No current room.", ephemeral=True)
            return

        feature = next(
            (f for f in room.features if f.name.lower() == feature_name.lower()), None
        )
        if feature is None:
            await interaction.followup.send(
                f"⚠ No feature named '{feature_name}' in this room.", ephemeral=True
            )
            return

        result = set_feature_state(state, feature.feature_id, new_state)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

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
            await interaction.followup.send(
                f"⚠ No NPC named '{npc_name}'.", ephemeral=True
            )
            return

        result = set_npc_status(state, npc.npc_id, status.lower())
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

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
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

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
            await interaction.followup.send(
                f"⚠ Unknown door state '{door_state}'. Valid: {valid}", ephemeral=True
            )
            return

        result = add_exit(state, label, description, ds, notes)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

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
            await interaction.followup.send("⚠ No current room.", ephemeral=True)
            return

        idx = exit_number - 1
        if idx < 0 or idx >= len(room.exits):
            await interaction.followup.send(
                f"⚠ Exit {exit_number} does not exist.", ephemeral=True
            )
            return

        try:
            ds = DoorState(door_state.lower())
        except ValueError:
            valid = [d.value for d in DoorState]
            await interaction.followup.send(
                f"⚠ Unknown door state '{door_state}'. Valid: {valid}", ephemeral=True
            )
            return

        result = set_exit_state(state, room.exits[idx].exit_id, ds)
        if not result.ok:
            await interaction.followup.send(f"⚠ {result.error}", ephemeral=True)
            return

        await update_status(interaction.channel, state)


# ---------------------------------------------------------------------------
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
