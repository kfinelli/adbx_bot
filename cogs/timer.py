"""
cogs/timer.py — Background task for turn timer expiry.

Checks every 60 seconds for open turns whose due_at has passed.
On expiry:
  - Closes the turn (CLOSED state, ready for DM resolution)
  - Posts a visible channel notification pinging the DM
  - Attempts a DM to the DM user; falls back silently if DMs are disabled
  - Updates the status block
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone

from engine import close_turn
from models import TurnStatus
from store import db, get_session, update_status, save_session_async


class TimerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_timers.start()

    def cog_unload(self):
        self.check_timers.cancel()

    @tasks.loop(seconds=60)
    async def check_timers(self):
        now = datetime.now(timezone.utc)

        for channel_id in db.list_channels():
            state = get_session(channel_id)
            if state is None:
                continue

            if not state.session_active:
                continue

            turn = state.current_turn
            if turn is None:
                continue
            if turn.status != TurnStatus.OPEN:
                continue
            if turn.due_at is None:
                continue

            # Ensure due_at is timezone-aware for comparison
            due = turn.due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)

            if now < due:
                continue

            # Timer has expired — close the turn
            close_turn(state)
            await save_session_async(state)

            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                continue

            # Build notification text with DM ping
            dm_mention = f"<@{state.dm_user_id}>" if state.dm_user_id else "DM"
            notification = (
                f"Turn {turn.turn_number} timer expired — "
                f"awaiting resolution ({dm_mention})."
            )

            await channel.send(notification)
            await update_status(channel, state)

            # Attempt a DM to the DM user
            if state.dm_user_id:
                try:
                    dm_user = await self.bot.fetch_user(int(state.dm_user_id))
                    await dm_user.send(
                        f"Turn {turn.turn_number} has expired in "
                        f"<#{channel_id}>. Ready for your resolution."
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass  # DMs disabled or user not found — channel ping is enough

    @check_timers.before_loop
    async def before_check_timers(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TimerCog(bot))
