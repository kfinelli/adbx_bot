"""
store.py — In-memory session registry, Discord status message helpers,
and persistence integration.

Sessions are keyed by Discord channel ID (str).
The Database is the source of truth on disk; the in-memory dict is a cache.
"""

from __future__ import annotations

import discord
from typing import Optional

from models import GameState, Party
from engine import render_status
from persistence import Database

# ---------------------------------------------------------------------------
# Database (single shared instance for the whole bot process)
# ---------------------------------------------------------------------------

db = Database("dungeon.db")

# ---------------------------------------------------------------------------
# In-memory session cache
# ---------------------------------------------------------------------------

# channel_id (str) -> GameState
_sessions: dict[str, GameState] = {}

# channel_id (str) -> discord.Message  (the pinned status message)
_status_messages: dict[str, discord.Message] = {}


def get_session(channel_id: str) -> Optional[GameState]:
    # Serve from cache if available
    if channel_id in _sessions:
        return _sessions[channel_id]
    # Try loading from DB (covers bot restarts)
    state = db.load(channel_id)
    if state is not None:
        _sessions[channel_id] = state
    return state


def create_session(channel_id: str, dm_user_id: str) -> GameState:
    state = GameState(
        platform_channel_id=channel_id,
        dm_user_id=dm_user_id,
    )
    state.party = Party(name="The Party")
    _sessions[channel_id] = state
    db.save(state)
    return state


def has_session(channel_id: str) -> bool:
    return get_session(channel_id) is not None


def save_session(state: GameState) -> None:
    """Persist the current state to disk. Call after every mutation."""
    db.save(state)


# ---------------------------------------------------------------------------
# Status message management
# ---------------------------------------------------------------------------

async def _post_fresh_status(channel: discord.TextChannel, state: GameState) -> None:
    """Post a new status message at the bottom of the channel and pin it."""
    content = f"```\n{render_status(state)}\n```"
    msg = await channel.send(content)
    _status_messages[str(channel.id)] = msg
    try:
        await msg.pin()
    except discord.Forbidden:
        pass


async def update_status(
    channel: discord.TextChannel,
    state: GameState,
) -> None:
    """
    Silently edit the existing status message in place.
    Used for most commands — no new message appears in the channel.
    Saves state to DB first.
    """
    save_session(state)
    content = f"```\n{render_status(state)}\n```"
    existing = _status_messages.get(str(channel.id))
    if existing is not None:
        try:
            await existing.edit(content=content)
            return
        except discord.NotFound:
            pass
    await _post_fresh_status(channel, state)


async def repost_status(
    channel: discord.TextChannel,
    state: GameState,
    narrative: str | None = None,
) -> None:
    """
    Delete the old status message, optionally post narrative text,
    then post a fresh status block at the bottom of the channel.
    Used by /dm_resolve so the channel reads: narrative -> status.
    Saves state to DB first.
    """
    save_session(state)

    existing = _status_messages.pop(str(channel.id), None)
    if existing is not None:
        try:
            await existing.unpin()
            await existing.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    if narrative:
        await channel.send(narrative)

    await _post_fresh_status(channel, state)


async def restore_status_message(bot: discord.Client, channel_id: str) -> None:
    """
    Called on bot startup for each saved session.
    Finds the most recent pinned bot message in the channel and re-registers
    it as the status message so future edits work correctly.
    """
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return
    state = get_session(channel_id)
    if state is None:
        return
    try:
        pins = await channel.pins()
        for msg in pins:
            if msg.author == bot.user and msg.content.startswith("```"):
                _status_messages[channel_id] = msg
                # Update it immediately so it reflects the restored state
                await msg.edit(content=f"```\n{render_status(state)}\n```")
                return
    except discord.Forbidden:
        pass
    # No existing pin found — post a fresh one
    msg = await channel.send(f"```\n{render_status(state)}\n```")
    _status_messages[channel_id] = msg
    try:
        await msg.pin()
    except discord.Forbidden:
        pass


# ---------------------------------------------------------------------------
# Interaction helpers
# ---------------------------------------------------------------------------

async def require_session(interaction: discord.Interaction) -> Optional[GameState]:
    state = get_session(str(interaction.channel_id))
    if state is None:
        await interaction.response.send_message(
            "No active session in this channel. Use /embark to start one.",
            ephemeral=True,
        )
        return None
    return state


async def ack(interaction: discord.Interaction) -> None:
    """
    Silently acknowledge a slash command interaction (ephemeral, no visible text).
    Discord requires every interaction to be acknowledged within 3 seconds.
    Use this for commands where the only feedback is the status block updating.
    """
    await interaction.response.defer(ephemeral=True)


async def err(interaction: discord.Interaction, message: str) -> None:
    """Send an ephemeral error message visible only to the invoking user."""
    await interaction.response.send_message(f"\u26a0 {message}", ephemeral=True)
