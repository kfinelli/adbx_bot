"""
store.py — In-memory session registry, Discord status message helpers,
and persistence integration.

Sessions are keyed by Discord channel ID (str).
The Database is the source of truth on disk; the in-memory dict is a cache.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import discord

from engine import render_status, render_status_header
from models import GameState, Party
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


def get_session(channel_id: str) -> GameState | None:
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
    """
    Hard-delete a session from memory and database entirely.
    Prefer archive_session() unless you genuinely want no trace.
    """
    _sessions.pop(channel_id, None)
    _status_messages.pop(channel_id, None)
    db.delete(channel_id)


async def archive_session(channel_id: str, channel_name: str = "") -> bool:
    """
    Move the active session for channel_id into the archive, clearing it
    from the active sessions table and the in-memory cache.
    Returns True if a session was found and archived, False otherwise.
    """
    _sessions.pop(channel_id, None)
    _status_messages.pop(channel_id, None)
    return await db.archive_async(channel_id, channel_name)


def has_session(channel_id: str) -> bool:
    return get_session(channel_id) is not None


def save_session(state: GameState) -> None:
    """
    Sync save — safe for startup, tests, and contexts where no event loop is running.
    In async handlers (slash commands, web routes, timer) use save_session_async().
    """
    db.save(state)


async def save_session_async(state: GameState) -> None:
    """Async save — use this from all coroutines to go through the DB lock."""
    await db.save_async(state)


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
    await save_session_async(state)
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
    await save_session_async(state)
    if narrative:
        await channel.send(narrative)
    await _post_fresh_status(channel, state)


# How far back to look for an existing status message on restart.
# If the most recent bot status post is older than this, post fresh instead.
_RESTORE_WINDOW_HOURS = 24


async def restore_status_message(bot: discord.Client, channel_id: str) -> None:
    """
    Called on bot startup for each saved session.

    Scans recent channel history for a status message posted by the bot
    within the last _RESTORE_WINDOW_HOURS hours. If found, re-registers it
    so the next update_status call edits it in place (no new message, no
    player notification). Falls back to posting fresh if nothing recent
    is found — e.g. after a long downtime or the first-ever start.
    """
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return
    state = get_session(channel_id)
    if state is None:
        return

    cutoff = datetime.now(UTC) - timedelta(hours=_RESTORE_WINDOW_HOURS)

    try:
        candidates = []
        async for msg in channel.history(limit=50):
            if msg.author != bot.user:
                continue
            if "```" not in msg.content:
                continue
            msg_ts = msg.created_at
            if msg_ts.tzinfo is None:
                msg_ts = msg_ts.replace(tzinfo=UTC)
            if msg_ts >= cutoff:
                candidates.append((msg_ts, msg))

        if candidates:
            _, best = max(candidates, key=lambda x: x[0])
            _status_messages[channel_id] = best
            await best.edit(content=_build_content(state))
            return
    except discord.Forbidden:
        pass

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
        mentions.append(f"<@{char.owner_id}>")
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

async def require_session(interaction: discord.Interaction) -> GameState | None:
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
    Acknowledge a slash command interaction with a brief ephemeral message.
    The message is deleted on success via ack_done(), or replaced with an
    error via ack_err(). This keeps the channel clean for all outcomes.
    """
    await interaction.response.send_message("Command received.", ephemeral=True)


async def ack_done(interaction: discord.Interaction) -> None:
    """Delete the 'Command received' ephemeral on successful completion."""
    with contextlib.suppress(discord.NotFound, discord.HTTPException):
        # already gone or token expired — silently ignore
        await interaction.delete_original_response()


async def ack_err(interaction: discord.Interaction, message: str) -> None:
    """Replace the 'Command received' ephemeral with an error message."""
    try:
        await interaction.edit_original_response(content=f"⚠ {message}")
    except (discord.NotFound, discord.HTTPException):
        # Token expired or message gone — fall back to a new followup
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(f"⚠ {message}", ephemeral=True)


async def err(interaction: discord.Interaction, message: str) -> None:
    """Send an ephemeral error message visible only to the invoking user."""
    await interaction.response.send_message(f"⚠ {message}", ephemeral=True)
