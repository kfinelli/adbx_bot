"""
test_startup.py — Fully automated: bot loads cleanly and registers commands.

No human interaction required.
"""

import pytest

EXPECTED_COGS = {"ArriveCog", "SessionCog", "DMCog", "TimerCog", "ActionButtonsCog"}

EXPECTED_COMMANDS = {
    "arrive", "turn", "round", "oracle",
    "abscond", "say", "emote", "strife", "help", "status",
    "dm_newsession", "dm_strife", "dm_say", "dm_emote",
}


@pytest.mark.discord_integration
async def test_all_cogs_loaded(bot):
    """Every cog in the expected set is registered on the bot."""
    loaded = set(bot.cogs.keys())
    assert loaded == EXPECTED_COGS, (
        f"Cog mismatch.\n  Missing: {EXPECTED_COGS - loaded}\n  Extra: {loaded - EXPECTED_COGS}"
    )


@pytest.mark.discord_integration
async def test_slash_commands_registered(bot):
    """All expected slash commands are present in the command tree."""
    import discord

    from tests.discord_integration._config import GUILD_ID
    registered = {c.name for c in bot.tree.get_commands(guild=discord.Object(id=GUILD_ID))}
    # Fall back to global commands if guild-scoped commands aren't synced yet.
    registered |= {c.name for c in bot.tree.get_commands()}
    missing = EXPECTED_COMMANDS - registered
    assert not missing, f"Slash commands not registered: {missing}"


@pytest.mark.discord_integration
async def test_bot_can_post_to_test_channel(bot, test_channel):
    """Bot has permission to post in the test channel."""
    msg = await test_channel.send("Integration test probe — safe to ignore.")
    assert msg.id is not None
    # Cleanup handled by _clean_channel fixture.
