"""
persistence.py — SQLite-backed save/load for GameState.

Database schema (single table):

    sessions
    --------
    channel_id   TEXT PRIMARY KEY   -- Discord channel ID
    session_id   TEXT               -- GameState.session_id (UUID string)
    dm_user_id   TEXT               -- Discord user ID of DM
    updated_at   TEXT               -- ISO 8601 timestamp of last save
    state_json   TEXT               -- Full serialized GameState

Usage:
    db = Database("dungeon.db")
    db.save(state)
    state = db.load(channel_id)
    all_ids = db.list_channels()
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from models import GameState
from serialization import deserialize_state, serialize_state

# Use a UTC-aware now() throughout to avoid the deprecation warning.
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str = "dungeon.db"):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        """Create tables if they don't exist yet."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                channel_id  TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                dm_user_id  TEXT,
                updated_at  TEXT NOT NULL,
                state_json  TEXT NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, state: GameState) -> None:
        """
        Persist a GameState. Inserts a new row or replaces the existing
        one for this channel.
        """
        self._conn.execute(
            """
            INSERT INTO sessions (channel_id, session_id, dm_user_id, updated_at, state_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                session_id = excluded.session_id,
                dm_user_id = excluded.dm_user_id,
                updated_at = excluded.updated_at,
                state_json = excluded.state_json
            """,
            (
                state.platform_channel_id,
                str(state.session_id),
                state.dm_user_id,
                _now_iso(),
                serialize_state(state),
            ),
        )
        self._conn.commit()

    def load(self, channel_id: str) -> Optional[GameState]:
        """
        Load a GameState for the given channel.
        Returns None if no session exists for that channel.
        """
        row = self._conn.execute(
            "SELECT state_json FROM sessions WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        return deserialize_state(row["state_json"])

    def delete(self, channel_id: str) -> None:
        """Remove a session from the database."""
        self._conn.execute(
            "DELETE FROM sessions WHERE channel_id = ?",
            (channel_id,),
        )
        self._conn.commit()

    def list_channels(self) -> list[str]:
        """Return all channel IDs that have a saved session."""
        rows = self._conn.execute(
            "SELECT channel_id FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [row["channel_id"] for row in rows]

    def close(self) -> None:
        self._conn.close()
