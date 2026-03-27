"""
conftest.py — fixtures for live Discord integration tests.

These tests connect the real bot to your Discord server and require:
  DISCORD_TOKEN            — the bot token (same as production)
  DISCORD_GUILD_ID         — your guild ID
  DISCORD_TEST_CHANNEL_ID  — a dedicated test channel in that guild
  DISCORD_TEST_DM_USER_ID  — your own Discord user ID (acts as DM in sessions)

Run with:
  pytest tests/discord_integration/ -m discord_integration -s -v

The -s flag keeps stdout live so you can read prompts in interactive tests.
"""

import asyncio
import contextlib
import os
import sys

import discord
import pytest
from discord.ext import commands

# Make project root importable when running this directory directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tests.discord_integration._config import GUILD_ID, TEST_CHANNEL_ID, TEST_DM_USER_ID, TOKEN


def _check_env():
    missing = [
        name for name, val in [
            ("DISCORD_TOKEN",           TOKEN),
            ("DISCORD_GUILD_ID",        GUILD_ID),
            ("DISCORD_TEST_CHANNEL_ID", TEST_CHANNEL_ID),
            ("DISCORD_TEST_DM_USER_ID", TEST_DM_USER_ID),
        ]
        if not val
    ]
    return missing


# ---------------------------------------------------------------------------
# Temp database — keeps integration tests off the production dungeon.db
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _test_db_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("discord_tests") / "test.db")


@pytest.fixture(scope="session", autouse=True)
def _patch_store_db(_test_db_path):
    """Replace the production db with a temp one for the whole test session."""
    from persistence import Database
    import store
    original = store.db
    store.db = Database(_test_db_path)
    yield
    store.db = original


# ---------------------------------------------------------------------------
# Bot fixture — starts the bot once for the whole session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
async def bot(_patch_store_db):
    missing = _check_env()
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")

    intents = discord.Intents.default()
    intents.message_content = True
    _bot = commands.Bot(command_prefix="!", intents=intents)

    await _bot.load_extension("cogs.arrive")
    await _bot.load_extension("cogs.session")
    await _bot.load_extension("cogs.dm_commands")
    await _bot.load_extension("cogs.timer")
    await _bot.load_extension("cogs.action_buttons")

    # Use our own Event driven from on_ready rather than wait_until_ready().
    # wait_until_ready() raises RuntimeError if called before bot.start() has
    # had a chance to initialise bot._ready internally (which happens during
    # the first few awaits inside login()). on_ready fires only after the
    # internal cache is fully populated, so this is both correct and safe.
    _ready = asyncio.Event()

    @_bot.event
    async def on_ready():
        _ready.set()

    task = asyncio.create_task(_bot.start(TOKEN))
    await asyncio.wait_for(_ready.wait(), timeout=30.0)

    yield _bot

    await _bot.close()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Channel fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
async def test_channel(bot):
    channel = bot.get_channel(TEST_CHANNEL_ID)
    assert channel is not None, (
        f"Channel {TEST_CHANNEL_ID} not found — "
        "check DISCORD_TEST_CHANNEL_ID and that the bot is in the guild."
    )
    return channel


# ---------------------------------------------------------------------------
# Per-test cleanup — delete any messages the bot posted during the test.
#
# NOTE: this must be an ordinary async test-function-level fixture, NOT
# called from inside another async fixture. aiohttp's TimerContext requires
# asyncio.current_task() to return non-None, which is only true inside a
# Task — and pytest-asyncio only wraps *test functions* as Tasks, not
# fixture coroutines. Cleanup is therefore done as a helper coroutine that
# tests (or other Tasks) call directly.
# ---------------------------------------------------------------------------

async def purge_bot_messages(channel, bot, limit: int = 50) -> None:
    """Delete recent messages posted by the bot in channel. Call from tests."""
    await channel.purge(limit=limit, check=lambda m: m.author == bot.user)
