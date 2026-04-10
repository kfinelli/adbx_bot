"""
test_session.py — Fully automated: session lifecycle through real Discord.

Creates and tears down sessions via the engine, drives the bot's status
posting through the store layer, and asserts on what actually appears in
the channel. No human interaction required.
"""

import pytest

from engine import create_character, open_turn, start_session
from models import CharacterClass, SessionMode, TurnStatus
from tests.discord_integration._config import TEST_CHANNEL_ID, TEST_DM_USER_ID


def _channel_id() -> str:
    return str(TEST_CHANNEL_ID)


@pytest.fixture
async def session(bot, test_channel):
    """Create a fresh PRE_START session for the test channel and clean it up after."""
    from store import create_session, delete_session
    from tests.discord_integration.conftest import purge_bot_messages
    state = create_session(_channel_id(), TEST_DM_USER_ID)
    yield state
    delete_session(_channel_id(), keep_characters=False)
    await purge_bot_messages(test_channel, bot)


@pytest.fixture
async def started_session(bot, test_channel):
    """Create a started EXPLORATION session with one character and clean it up after."""
    from store import create_session, delete_session
    from tests.discord_integration.conftest import purge_bot_messages
    state = create_session(_channel_id(), TEST_DM_USER_ID)
    create_character(state, name="Testchar", character_class=CharacterClass.KNIGHT,
                     equipment_package="", owner_id=TEST_DM_USER_ID)
    start_session(state)
    yield state
    delete_session(_channel_id(), keep_characters=False)
    await purge_bot_messages(test_channel, bot)


# ---------------------------------------------------------------------------
# Status message content
# ---------------------------------------------------------------------------

@pytest.mark.discord_integration
async def test_pre_start_status_posts_to_channel(bot, test_channel, session):
    """A PRE_START session posts 'Awaiting players' to the channel."""
    from store import update_status
    await update_status(test_channel, session)

    msgs = [m async for m in test_channel.history(limit=5) if m.author == bot.user]
    assert msgs, "Bot posted no messages"
    assert "Awaiting players" in msgs[0].content


@pytest.mark.discord_integration
async def test_exploration_status_shows_mode(bot, test_channel, started_session):
    """After start_session, status shows 'Exploration'."""
    from store import update_status
    await update_status(test_channel, started_session)

    msgs = [m async for m in test_channel.history(limit=5) if m.author == bot.user]
    assert msgs
    assert "Exploration" in msgs[0].content


@pytest.mark.discord_integration
async def test_open_turn_appears_in_status(bot, test_channel, started_session):
    """An open turn shows 'accepting turn submissions' in the status."""
    from store import update_status
    open_turn(started_session)
    await update_status(test_channel, started_session)

    msgs = [m async for m in test_channel.history(limit=5) if m.author == bot.user]
    assert msgs
    content = msgs[0].content
    assert started_session.mode == SessionMode.EXPLORATION
    assert started_session.current_turn.status == TurnStatus.OPEN
    assert "accepting" in content.lower()


@pytest.mark.discord_integration
async def test_status_edits_in_place(bot, test_channel, started_session):
    """update_status edits the existing message rather than posting a new one."""
    from store import update_status

    await update_status(test_channel, started_session)
    msgs_before = [m async for m in test_channel.history(limit=10) if m.author == bot.user]
    assert len(msgs_before) == 1
    first_msg_id = msgs_before[0].id

    open_turn(started_session)
    await update_status(test_channel, started_session)
    msgs_after = [m async for m in test_channel.history(limit=10) if m.author == bot.user]

    # Still exactly one message (edited, not a new post)
    assert len(msgs_after) == 1
    assert msgs_after[0].id == first_msg_id
    assert "accepting" in msgs_after[0].content.lower()


@pytest.mark.discord_integration
async def test_session_delete_cleans_up(bot, test_channel):
    """Deleting a session removes it from the store (nothing persists)."""
    from store import create_session, delete_session, get_session
    _state = create_session(_channel_id(), TEST_DM_USER_ID)
    assert get_session(_channel_id()) is not None

    delete_session(_channel_id(), keep_characters=False)
    assert get_session(_channel_id()) is None
