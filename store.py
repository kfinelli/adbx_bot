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
from engine import render_status, render_status_header
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

# channel_id (str) -> discord.Message  (the live status message)
_status_messages: dict[str, discord.Message] = {}


def get_session(channel_id: str) -> Optional[GameState]:
    if channel_id in _sessions:
        return _sessions[channel_id]
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


def delete_session(channel_id: str) -> None:
    """Remove a session from memory and the database entirely."""
    _sessions.pop(channel_id, None)
    _status_messages.pop(channel_id, None)
    db.delete(channel_id)


def has_session(channel_id: str) -> bool:
    return get_session(channel_id) is not None


def save_session(state: GameState) -> None:
    """Persist the current state to disk. Call after every mutation."""
    db.save(state)


# ---------------------------------------------------------------------------
# Status message helpers
# ---------------------------------------------------------------------------

def _build_content(state: GameState) -> str:
    header = render_status_header(state)
    body = render_status(state)
    return header + "\n```\n" + body + "\n```"


async def _post_fresh_status(channel: discord.TextChannel, state: GameState) -> None:
    """Post a new status message at the bottom of the channel."""
    msg = await channel.send(_build_content(state))
    _status_messages[str(channel.id)] = msg


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
    existing = _status_messages.get(str(channel.id))
    if existing is not None:
        try:
            await existing.edit(content=_build_content(state))
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
    Post narrative text (if any) then a fresh status block, leaving the
    old status message in place. This creates a rolling log in the channel:
      Turn N status block
      [player submissions, oracle posts, DM notifications]
      Resolution narrative
      Turn N+1 status block
      ...
    Saves state to DB first.
    """
    save_session(state)
    if narrative:
        await channel.send(narrative)
    await _post_fresh_status(channel, state)


async def restore_status_message(bot: discord.Client, channel_id: str) -> None:
    """
    Called on bot startup for each saved session.
    Posts a fresh status block so the current state is always visible
    at the bottom of the channel after a restart.
    """
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return
    state = get_session(channel_id)
    if state is None:
        return
    await _post_fresh_status(channel, state)


# ---------------------------------------------------------------------------
# Player new-turn ping
# ---------------------------------------------------------------------------

async def notify_players_new_turn(
    channel: discord.TextChannel,
    state: GameState,
) -> None:
    """
    Ping every active character owner when a new turn opens.
    Uses stored owner_id (Discord user ID) so no role is needed.
    """
    if not state.party or not state.characters:
        return
    mentions = []
    seen = set()
    for char_id in state.party.member_ids:
        char = state.characters.get(char_id)
        if char is None:
            continue
        if char.status.value == "dead":
            continue
        if not char.owner_id or char.owner_id in seen:
            continue
        seen.add(char.owner_id)
        mentions.append("<@{}>".format(char.owner_id))
    if not mentions:
        return
    mode = "Round" if state.mode.value == "rounds" else "Turn"
    await channel.send(
        "{} — {} {} is ready!".format(
            " ".join(mentions), mode, state.turn_number
        )
    )


# ---------------------------------------------------------------------------
# DM turn-close notification
# ---------------------------------------------------------------------------

async def notify_dm_of_turn_close(bot_or_channel, state: GameState, turn_number: int) -> None:
    """
    Post a visible channel notification that the turn has closed early
    (all players submitted or party leader used /abscond) and attempt
    a DM to the DM user.
    Called by the platform layer when EngineResult.notify_dm is True.
    """
    # bot_or_channel can be a TextChannel directly
    channel = bot_or_channel
    dm_mention = f"<@{state.dm_user_id}>" if state.dm_user_id else "DM"
    await channel.send(
        f"All turns submitted — Turn {turn_number} ready for resolution ({dm_mention})."
    )

    if state.dm_user_id:
        try:
            import discord as _discord
            # Fetch user via the channel's guild
            dm_user = await channel.guild.fetch_member(int(state.dm_user_id))
            await dm_user.send(
                f"All turns submitted in <#{state.platform_channel_id}>. "
                f"Turn {turn_number} is ready for your resolution."
            )
        except Exception:
            pass  # DMs disabled or member not found — channel ping is enough


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
    """
    await interaction.response.defer(ephemeral=True)


async def err(interaction: discord.Interaction, message: str) -> None:
    """Send an ephemeral error message visible only to the invoking user."""
    await interaction.response.send_message(f"⚠ {message}", ephemeral=True)
