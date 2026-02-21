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
