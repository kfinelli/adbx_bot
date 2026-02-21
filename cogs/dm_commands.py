"""
cogs/dm_commands.py — DM-facing slash commands.

All commands are prefixed /dm_ and check that the invoking user
is the session DM before proceeding.

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
    CharacterClass,
    CharacterStatus,
    DoorState,
    Exit,
    NPC,
    Room,
    RoomFeature,
)
from engine import (
    add_npc,
    close_turn,
    enter_rounds,
    exit_rounds,
    open_turn,
    resolve_turn,
    set_character_hp,
    set_character_status,
    set_feature_state,
    set_light_source,
    set_npc_hp,
    set_npc_status,
    set_room,
)
from store import (
    err,
    get_session,
    ok,
    post_or_update_status,
    require_session,
)


class DMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # DM guard
    # ------------------------------------------------------------------

    async def _require_dm(self, interaction: discord.Interaction):
        """
        Return the session if the invoking user is the DM, else send an
        error and return None.
        """
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
        description="[DM] Write the turn resolution and advance to the next turn.",
    )
    @app_commands.describe(narrative="Describe what happens as a result of the players' actions.")
    async def dm_resolve(self, interaction: discord.Interaction, narrative: str):
        state = await self._require_dm(interaction)
        if state is None:
            return

        # Close then resolve
        if state.current_turn is None:
            await err(interaction, "No open turn to resolve.")
            return

        close_turn(state)
        result = resolve_turn(state, narrative)
        if not result.ok:
            await err(interaction, result.error)
            return

        # Open the next turn automatically
        open_turn(state)

        await ok(interaction, f"📜 **Turn resolved:**\n{narrative}")
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_sethp
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_sethp",
        description="[DM] Set HP for a character or NPC by name.",
    )
    @app_commands.describe(
        target_name="Name of the character or NPC",
        hp="New HP value",
    )
    async def dm_sethp(self, interaction: discord.Interaction, target_name: str, hp: int):
        state = await self._require_dm(interaction)
        if state is None:
            return

        # Try characters first
        char = _find_char_by_name(state, target_name)
        if char is not None:
            result = set_character_hp(state, char.character_id, hp)
        else:
            npc = _find_npc_by_name(state, target_name)
            if npc is None:
                await err(interaction, f"No character or NPC named '{target_name}'.")
                return
            result = set_npc_hp(state, npc.npc_id, hp)

        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setstatus
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setstatus",
        description="[DM] Set a character's status and optional notes.",
    )
    @app_commands.describe(
        character_name="Character name",
        status="Status (active, dead, fled, petrified, paralyzed)",
        notes="Optional notes shown in status block (e.g. 'fatigued', 'poisoned')",
    )
    async def dm_setstatus(
        self,
        interaction: discord.Interaction,
        character_name: str,
        status: str,
        notes: str = "",
    ):
        state = await self._require_dm(interaction)
        if state is None:
            return

        char = _find_char_by_name(state, character_name)
        if char is None:
            await err(interaction, f"No character named '{character_name}'.")
            return

        try:
            char_status = CharacterStatus(status.lower())
        except ValueError:
            valid = [s.value for s in CharacterStatus]
            await err(interaction, f"Unknown status '{status}'. Valid: {valid}")
            return

        result = set_character_status(state, char.character_id, char_status, notes)
        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)

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
        notes="DM-facing notes (hidden from players)",
    )
    async def dm_setroom(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str,
        notes: str = "",
    ):
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = Room(name=name, description=description, notes=notes)
        result = set_room(state, room)
        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, f"🚪 Moved party to **{name}**.")
        await post_or_update_status(interaction.channel, state)

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
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = state.current_room
        if room is None:
            await err(interaction, "No current room. Use /dm_setroom first.")
            return

        feature = RoomFeature(name=name, description=description, state=state_str)
        room.features.append(feature)

        await ok(interaction, f"Feature added: **{name}**.")
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setfeature
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setfeature",
        description="[DM] Update the state of a room feature.",
    )
    @app_commands.describe(
        feature_name="Name of the feature to update",
        new_state="New state description (e.g. 'smashed', 'unlocked')",
    )
    async def dm_setfeature(
        self,
        interaction: discord.Interaction,
        feature_name: str,
        new_state: str,
    ):
        state = await self._require_dm(interaction)
        if state is None:
            return

        room = state.current_room
        if room is None:
            await err(interaction, "No current room.")
            return

        feature = next(
            (f for f in room.features if f.name.lower() == feature_name.lower()), None
        )
        if feature is None:
            await err(interaction, f"No feature named '{feature_name}' in this room.")
            return

        result = set_feature_state(state, feature.feature_id, new_state)
        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)

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
        ac="Armor class (descending, e.g. 6)",
        description="Brief description shown in status block",
        damage_dice="Damage dice string (e.g. 1d6)",
        notes="DM-facing notes (hidden from players)",
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
        result = add_npc(state, npc)
        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setnpcstatus
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setnpcstatus",
        description="[DM] Update an NPC's status (e.g. dead, fled, charmed).",
    )
    @app_commands.describe(
        npc_name="Name of the NPC",
        status="New status string",
    )
    async def dm_setnpcstatus(
        self,
        interaction: discord.Interaction,
        npc_name: str,
        status: str,
    ):
        state = await self._require_dm(interaction)
        if state is None:
            return

        npc = _find_npc_by_name(state, npc_name)
        if npc is None:
            await err(interaction, f"No NPC named '{npc_name}'.")
            return

        result = set_npc_status(state, npc.npc_id, status.lower())
        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)

    # ------------------------------------------------------------------
    # /dm_setlight
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dm_setlight",
        description="[DM] Set the active light source.",
    )
    @app_commands.describe(
        label="Description of the light source (e.g. 'Torch', 'Lantern')",
        turns="Remaining turns (leave blank for permanent/magical)",
    )
    async def dm_setlight(
        self,
        interaction: discord.Interaction,
        label: str,
        turns: int = -1,
    ):
        state = await self._require_dm(interaction)
        if state is None:
            return

        turns_remaining = None if turns < 0 else turns
        result = set_light_source(state, label, turns_remaining)
        if not result.ok:
            await err(interaction, result.error)
            return

        await ok(interaction, result.message)
        await post_or_update_status(interaction.channel, state)


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
