"""
Async Dungeon Crawler — Discord Bot
Entry point. Run with: python bot.py
"""

import asyncio
import contextlib
import logging
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

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# LOG_LEVEL env var controls verbosity (default INFO).
# Set LOG_LEVEL=DEBUG in .env to see detailed arrive.py diagnostics.
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Quieten noisy third-party loggers
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("python_multipart").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

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
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await message.delete()


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    log.info("Target guild ID: %s", GUILD_ID)

    bot.tree.copy_global_to(guild=GUILD)
    try:
        synced = await bot.tree.sync(guild=GUILD)
        log.info("Synced %d slash command(s) to guild %s.", len(synced), GUILD_ID)
    except discord.Forbidden as e:
        log.error("Forbidden — check bot has 'applications.commands' scope: %s", e)
    except Exception as e:
        log.error("Failed to sync commands: %s", e)

    # Inject bot reference into web UI
    from webui.app import set_bot
    set_bot(bot)

    # Restore status messages for all saved sessions
    from datetime import datetime

    from engine import close_turn
    from store import db, get_session, restore_status_message, save_session

    channel_ids = db.list_channels()
    log.info("Restoring %d saved session(s)...", len(channel_ids))
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
            log.info("Channel %s: closing expired turn %s", channel_id, turn.turn_number)
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

    log.info("Sessions restored.")
    log.info("DM panel available at http://localhost:%s/", WEB_PORT)


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
        await bot.load_extension("cogs.slash_commands")
        await bot.load_extension("cogs.dm_commands")
        await bot.load_extension("cogs.timer")
        await bot.load_extension("cogs.action_buttons")
        # Run bot and web UI concurrently in the same event loop
        await asyncio.gather(
            bot.start(TOKEN),
            start_webui(),
        )


if __name__ == "__main__":
    asyncio.run(main())
