"""Shared constants for Discord integration tests, read from environment."""
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN            = os.environ.get("DISCORD_TOKEN")
GUILD_ID         = int(os.environ.get("DISCORD_GUILD_ID", "0"))
TEST_CHANNEL_ID  = int(os.environ.get("DISCORD_TEST_CHANNEL_ID", "0"))
TEST_DM_USER_ID  = os.environ.get("DISCORD_TEST_DM_USER_ID", "")
