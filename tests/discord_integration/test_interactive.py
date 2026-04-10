"""
test_interactive.py — Semi-automated tests requiring human Discord interaction.

Run with:
    pytest tests/discord_integration/test_interactive.py -m discord_integration -s -v

The -s flag is required so prompts print to your terminal.
Each test prints instructions, then waits for you to act in Discord.
Default timeout per prompt is 90 seconds.
"""

import asyncio

import discord
import pytest

from engine import create_character, open_turn, start_session
from models import CharacterClass
from tests.discord_integration._config import TEST_CHANNEL_ID, TEST_DM_USER_ID

TIMEOUT = 90  # seconds to wait for each human action
POLL_INTERVAL = 2  # seconds between store polls
_CHANNEL_ID = str(TEST_CHANNEL_ID)


def _prompt(msg: str):
    """Print a clearly visible prompt to the terminal."""
    print(f"\n{'='*60}")
    print("  ACTION REQUIRED")
    print(f"  {msg}")
    print(f"  (timeout: {TIMEOUT}s)")
    print(f"{'='*60}\n", flush=True)


async def _poll_until(condition, timeout=TIMEOUT, interval=POLL_INTERVAL):
    """
    Poll condition() every interval seconds until it returns truthy or timeout.
    Returns the truthy value, or None on timeout.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = condition()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


@pytest.fixture
def active_session():
    """
    A started EXPLORATION session in the test channel — sync only.
    No Discord API calls here; tests post the initial status themselves
    to stay inside a proper asyncio Task context.
    """
    from store import create_session, delete_session, save_session
    state = create_session(_CHANNEL_ID, TEST_DM_USER_ID)
    start_session(state)
    save_session(state)
    yield state
    delete_session(_CHANNEL_ID, keep_characters=False)


@pytest.fixture
def session_with_character():
    """
    A started EXPLORATION session with one pre-built character — sync only.
    Skips the interactive /arrive flow for tests that don't need to test it.
    """
    from store import create_session, delete_session, save_session
    state = create_session(_CHANNEL_ID, TEST_DM_USER_ID)
    create_character(
        state,
        name="Testchar",
        character_class=CharacterClass.KNIGHT,
        equipment_package="",
        owner_id=TEST_DM_USER_ID,
    )
    start_session(state)
    save_session(state)
    yield state
    delete_session(_CHANNEL_ID, keep_characters=False)


# ---------------------------------------------------------------------------
# /arrive — character creation flow
# ---------------------------------------------------------------------------

@pytest.mark.discord_integration
async def test_arrive_creates_character(bot, test_channel, active_session):
    """
    Human step: run /arrive in the test channel, roll stats, pick a class.
    The bot should update the channel status showing the new party member.
    """
    from store import get_session, update_status
    from tests.discord_integration.conftest import purge_bot_messages

    await update_status(test_channel, active_session)

    _prompt(
        f"Run /arrive in the test channel (#{test_channel.name}), "
        "accept your stats, and select a class."
    )

    def _character_present():
        state = get_session(_CHANNEL_ID)
        return state and state.characters

    try:
        result = await _poll_until(_character_present)
        if not result:
            pytest.fail(f"No character appeared in session within {TIMEOUT}s.")
    finally:
        await purge_bot_messages(test_channel, bot)

    state = get_session(_CHANNEL_ID)
    assert state is not None
    assert len(state.characters) == 1, f"Expected 1 character, got {len(state.characters)}"


# ---------------------------------------------------------------------------
# /turn — submission
# ---------------------------------------------------------------------------

@pytest.mark.discord_integration
async def test_turn_submission(bot, test_channel, session_with_character):
    """
    Human step: run /turn with any action text.
    The bot should record the submission in the session.
    """
    from store import get_session, save_session, update_status
    from tests.discord_integration.conftest import purge_bot_messages

    open_turn(session_with_character)
    save_session(session_with_character)
    await update_status(test_channel, session_with_character)

    _prompt(
        f"Run /turn in #{test_channel.name} with any action "
        "(e.g. /turn action:\"I look around\")."
    )

    def _turn_submitted():
        state = get_session(_CHANNEL_ID)
        if state is None or state.current_turn is None:
            return False
        return any(s for s in state.current_turn.submissions if s.is_latest)

    try:
        result = await _poll_until(_turn_submitted)
        if not result:
            pytest.fail(f"No turn submission found within {TIMEOUT}s.")
    finally:
        await purge_bot_messages(test_channel, bot)

    state = get_session(_CHANNEL_ID)
    assert state is not None
    assert state.current_turn is not None
    submissions = [s for s in state.current_turn.submissions if s.is_latest]
    assert submissions, "No turn submissions found after /turn command."


# ---------------------------------------------------------------------------
# "My Character" button — DM sheet delivery
# ---------------------------------------------------------------------------

@pytest.mark.discord_integration
async def test_character_command_responds(bot, test_channel, session_with_character):
    """
    Human step: click the "My Character" button on the status message.
    The bot should send the character sheet to the user's DMs.
    """
    from store import update_status
    from tests.discord_integration.conftest import purge_bot_messages

    await update_status(test_channel, session_with_character)

    timestamp_before = discord.utils.utcnow()

    _prompt(f"Click the 'My Character' button on the status message in #{test_channel.name}.")

    # The button sends the sheet via DM.  Poll the user's DM channel for a bot
    # message that arrived after the prompt was issued.
    async def _dm_sheet_received():
        user = await bot.fetch_user(int(TEST_DM_USER_ID))
        dm = await user.create_dm()
        async for m in dm.history(limit=10, after=timestamp_before):
            if m.author == bot.user:
                return m
        return None

    msg = None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + TIMEOUT
    while loop.time() < deadline:
        msg = await _dm_sheet_received()
        if msg:
            break
        await asyncio.sleep(POLL_INTERVAL)

    try:
        if msg is None:
            pytest.fail(f"No 'My Character' DM seen within {TIMEOUT}s.")
    finally:
        await purge_bot_messages(test_channel, bot)

    assert msg is not None
