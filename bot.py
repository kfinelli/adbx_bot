"""
Async Dungeon Crawler — Discord Bot
Entry point. Run with: python bot.py
"""

import os
import discord
from discord.ext import commands

# Load token from environment variable
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # Restore status messages for all saved sessions
    from store import db, restore_status_message
    channel_ids = db.list_channels()
    print(f"Restoring {len(channel_ids)} saved session(s)...")
    for channel_id in channel_ids:
        await restore_status_message(bot, channel_id)
    print("Sessions restored.")


async def main():
    async with bot:
        await bot.load_extension("cogs.session")
        await bot.load_extension("cogs.dm_commands")
        await bot.start(TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
