"""
session_cache.py — In-memory session registry (discord-free).

Separated from store.py so that persistence tests and other non-Discord code
can manipulate the session cache without triggering a discord import.
"""

from __future__ import annotations

# channel_id (str) -> GameState
_sessions: dict = {}


def sync_character_to_sessions(char) -> None:
    """Replace a character in every in-memory session that contains it.

    Call this after any standalone character update (addxp, equip, etc.) so
    that a later save_session_async() call doesn't overwrite the updated
    character with stale in-memory data.
    """
    for state in _sessions.values():
        if char.character_id in state.characters:
            state.characters[char.character_id] = char
