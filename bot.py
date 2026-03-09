"""
Async Dungeon Crawler — Discord Bot
Entry point. Run with: python bot.py
"""

import asyncio
import os
from datetime import UTC

import discord
import uvicorn
from discord.ext import commands

# Load .env file if present. Actual environment variables take precedence.
# pip install python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — fall back to environment variables only

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
if not GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID environment variable not set.")

GUILD = discord.Object(id=GUILD_ID)
WEB_PORT = int(os.environ.get("DM_PANEL_PORT", "8080"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_message(message: discord.Message):
    """
    Auto-delete any human-typed messages in active game channels.
    This keeps the channel clean since players must use slash commands.
    The bot's own messages are never deleted.
    Silently ignores channels with no active session.
    """
    # Never delete the bot's own messages
    if message.author == bot.user:
        return
    # Only act on guild text messages (not DMs)
    if not message.guild:
        return
    # Only delete if this channel has an active session
    from store import has_session
    if has_session(str(message.channel.id)):
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass  # missing permissions or already deleted — silently ignore


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Target guild ID: {GUILD_ID}")

    bot.tree.copy_global_to(guild=GUILD)
    try:
        synced = await bot.tree.sync(guild=GUILD)
        print(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID}.")
    except discord.Forbidden as e:
        print(f"Forbidden — check bot has 'applications.commands' scope: {e}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # Inject bot reference into web UI
    from webui.app import set_bot
    set_bot(bot)

    # Restore status messages for all saved sessions
    from datetime import datetime

    from engine import close_turn
    from store import db, get_session, restore_status_message, save_session

    channel_ids = db.list_channels()
    print(f"Restoring {len(channel_ids)} saved session(s)...")
    for channel_id in channel_ids:
        await restore_status_message(bot, channel_id)

        # Catch turns that expired while the bot was offline
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
            due = due.replace(tzinfo=UTC)
        if datetime.now(UTC) >= due:
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
    print(f"DM panel available at http://localhost:{WEB_PORT}/")


async def start_webui():
    """Run the FastAPI web UI inside the bot's event loop."""
    from webui.app import app as fastapi_app
    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=WEB_PORT,
        log_level="warning",   # keep terminal clean
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    async with bot:
        await bot.load_extension("cogs.arrive")
        await bot.load_extension("cogs.session")
        await bot.load_extension("cogs.dm_commands")
        await bot.load_extension("cogs.timer")
        # Run bot and web UI concurrently in the same event loop
        await asyncio.gather(
            bot.start(TOKEN),
            start_webui(),
        )


if __name__ == "__main__":
    asyncio.run(main())
