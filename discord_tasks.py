"""
discord_tasks.py — Async Discord side-effect helpers.

These functions encapsulate every "do something in Discord as a consequence
of a game event" action. Both the slash-command cogs and the web UI call
these rather than duplicating the Discord logic.

All functions are fire-and-forget coroutines intended to be called with
asyncio.create_task() from the web UI, or awaited directly from cogs.

Convention:
  - Functions accept a discord.Client `bot` and a discord.TextChannel
    `channel` where relevant.
  - Game-model objects (Oracle, GameState) are imported from models/store;
    no raw Discord IDs are constructed here.
"""

from __future__ import annotations

import contextlib

import discord

from models import GameState, LevelUpResult, Oracle

# ---------------------------------------------------------------------------
# Level-up notifications
# ---------------------------------------------------------------------------

async def dispatch_level_up(bot: discord.Client, character, results: list[LevelUpResult]) -> None:
    """DM the character's owner when they level up. Best-effort — failures are silent."""
    if not character.owner_id or not results:
        return
    lines = [f"**{character.name} reached Level {results[-1].new_level}!**"]
    for r in results:
        lines.append(f"HP +{r.hp_gained}")
        for stat, val in r.stat_changes.items():
            lines.append(f"{stat.upper()} +{val}")
    try:
        user = await bot.fetch_user(int(character.owner_id))
        await user.send("\n".join(lines))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

async def post_oracle_question(
    channel: discord.TextChannel,
    oracle: Oracle,
) -> discord.Message:
    """
    Post a new oracle question to the channel and return the message.
    The caller is responsible for storing message.id on the oracle.
    """
    return await channel.send(oracle.question_text)


async def dispatch_oracle_answer(
    bot: discord.Client,
    channel: discord.TextChannel,
    oracle: Oracle,
) -> None:
    """
    After an oracle has been answered:
      1. Edit the original channel message to include the answer.
      2. Send a private DM to the player who asked.

    Both steps are best-effort — failures are silently ignored so a
    closed DM or deleted message never breaks the flow.
    """
    # Edit the channel message in place
    if oracle.message_id:
        try:
            msg = await channel.fetch_message(oracle.message_id)
            await msg.edit(content=oracle.answer_text)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Message was deleted or bot lost permission — post a fallback
            with contextlib.suppress(discord.HTTPException):
                await channel.send(
                    f"**Oracle #{oracle.number}** (answer): {oracle.answer}"
                )

    # DM the player who asked
    if oracle.asker_owner_id:
        try:
            user = await bot.fetch_user(int(oracle.asker_owner_id))
            await user.send(oracle.player_dm_text)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass  # player has DMs closed or account not found


# ---------------------------------------------------------------------------
# Turn resolution
# ---------------------------------------------------------------------------

async def dispatch_turn_resolved(
    channel: discord.TextChannel,
    state: GameState,
    narrative: str,
) -> None:
    """
    After a turn is resolved:
      1. Post the narrative then a fresh status block.
      2. Ping all active players that the new turn is ready.

    Importing store here (not at module level) avoids a circular import
    since store.py imports from models.py.
    """
    from store import notify_players_new_turn, repost_status
    await repost_status(channel, state, narrative=narrative)
    await notify_players_new_turn(channel, state)
