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

import asyncio
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
        # Single lock shared across the bot event loop and the FastAPI coroutines,
        # which both run in the same asyncio event loop via asyncio.gather().
        # Prevents concurrent writes from the timer task and web UI routes
        # hitting SQLite simultaneously.
        self._lock = asyncio.Lock()
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

    # ------------------------------------------------------------------
    # Sync helpers (called from async wrappers below)
    # ------------------------------------------------------------------

    def _save_sync(self, state: GameState) -> None:
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

    def _load_sync(self, channel_id: str) -> Optional[GameState]:
        row = self._conn.execute(
            "SELECT state_json FROM sessions WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        return deserialize_state(row["state_json"])

    def _delete_sync(self, channel_id: str) -> None:
        self._conn.execute(
            "DELETE FROM sessions WHERE channel_id = ?",
            (channel_id,),
        )
        self._conn.commit()

    def _list_sync(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT channel_id FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [row["channel_id"] for row in rows]

    # ------------------------------------------------------------------
    # Public API — lock-guarded async versions
    # ------------------------------------------------------------------

    async def save_async(self, state: GameState) -> None:
        """Persist a GameState, serialising concurrent writes via the asyncio lock."""
        async with self._lock:
            self._save_sync(state)

    async def load_async(self, channel_id: str) -> Optional[GameState]:
        async with self._lock:
            return self._load_sync(channel_id)

    async def delete_async(self, channel_id: str) -> None:
        async with self._lock:
            self._delete_sync(channel_id)

    async def list_channels_async(self) -> list[str]:
        async with self._lock:
            return self._list_sync()

    # ------------------------------------------------------------------
    # Public API — sync convenience wrappers (safe for startup / tests)
    # ------------------------------------------------------------------

    def save(self, state: GameState) -> None:
        """
        Sync save. Safe to call at startup or from tests.
        In async contexts prefer save_async().
        """
        self._save_sync(state)

    def load(self, channel_id: str) -> Optional[GameState]:
        return self._load_sync(channel_id)

    def delete(self, channel_id: str) -> None:
        self._delete_sync(channel_id)

    def list_channels(self) -> list[str]:
        return self._list_sync()

    def close(self) -> None:
        self._conn.close()
