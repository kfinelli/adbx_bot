"""
Async Dungeon Crawler — Discord Bot
Entry point. Run with: python bot.py
"""

import os
import discord
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
if not GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID environment variable not set.")

GUILD = discord.Object(id=GUILD_ID)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Target guild ID: {GUILD_ID}")

    # Register all commands directly to the guild (instant, no global sync)
    bot.tree.copy_global_to(guild=GUILD)
    try:
        synced = await bot.tree.sync(guild=GUILD)
        print(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID}.")
    except discord.Forbidden as e:
        print(f"Forbidden — check bot has 'applications.commands' scope: {e}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # Restore status messages for all saved sessions
    from store import db, restore_status_message, save_session, notify_dm_of_turn_close
    from engine import close_turn
    from datetime import datetime, timezone

    channel_ids = db.list_channels()
    print(f"Restoring {len(channel_ids)} saved session(s)...")
    for channel_id in channel_ids:
        await restore_status_message(bot, channel_id)

        # Catch turns that expired while the bot was offline
        from store import get_session
        state = get_session(channel_id)
        if state is None:
            continue
        turn = state.current_turn
        if turn is None or turn.status.value != "open":
            continue
        if turn.due_at is None:
            continue
        due = turn.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= due:
            print(f"Channel {channel_id}: closing expired turn {turn.turn_number}")
            close_turn(state)
            save_session(state)
            channel = bot.get_channel(int(channel_id))
            if channel:
                dm_mention = f"<@{state.dm_user_id}>" if state.dm_user_id else "DM"
                await channel.send(
                    f"Turn {turn.turn_number} timer expired while bot was offline — "
                    f"awaiting resolution ({dm_mention})."
                )
                from store import update_status
                await update_status(channel, state)

    print("Sessions restored.")


async def main():
    async with bot:
        await bot.load_extension("cogs.session")
        await bot.load_extension("cogs.dm_commands")
        await bot.load_extension("cogs.timer")
        await bot.start(TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
