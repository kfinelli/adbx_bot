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
from datetime import UTC, datetime

from models import GameState
from serialization import deserialize_state, serialize_state


# Use a UTC-aware now() throughout to avoid the deprecation warning.
def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS archived_sessions (
                session_id   TEXT PRIMARY KEY,  -- GameState.session_id UUID
                channel_id   TEXT NOT NULL,     -- original channel
                channel_name TEXT,              -- human-readable name at archive time
                dm_user_id   TEXT,
                turn_number  INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT,
                updated_at   TEXT NOT NULL,
                archived_at  TEXT NOT NULL,
                state_json   TEXT NOT NULL
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

    def _load_sync(self, channel_id: str) -> GameState | None:
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

    def _archive_sync(self, channel_id: str, channel_name: str = "") -> bool:
        """
        Copy the active session for channel_id into archived_sessions,
        then delete it from sessions. Returns True if a row was archived,
        False if no session existed.
        """
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return False
        # Peek at turn_number and created_at from the JSON to store as metadata
        try:
            from serialization import deserialize_state as _ds
            state = _ds(row["state_json"])
            turn_number = state.turn_number
            created_at  = state.created_at.isoformat() if state.created_at else None
        except Exception:
            turn_number = 0
            created_at  = None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO archived_sessions
                (session_id, channel_id, channel_name, dm_user_id,
                 turn_number, created_at, updated_at, archived_at, state_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["session_id"],
                channel_id,
                channel_name,
                row["dm_user_id"],
                turn_number,
                created_at,
                row["updated_at"],
                _now_iso(),
                row["state_json"],
            ),
        )
        self._conn.execute(
            "DELETE FROM sessions WHERE channel_id = ?",
            (channel_id,),
        )
        self._conn.commit()
        return True

    def _list_archive_sync(self) -> list[dict]:
        """Return archive metadata rows, newest first."""
        rows = self._conn.execute(
            """
            SELECT session_id, channel_id, channel_name, dm_user_id,
                   turn_number, created_at, updated_at, archived_at
            FROM   archived_sessions
            ORDER  BY archived_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def _load_archive_sync(self, session_id: str) -> GameState | None:
        row = self._conn.execute(
            "SELECT state_json FROM archived_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        from serialization import deserialize_state as _ds
        return _ds(row["state_json"])

    def _delete_archive_sync(self, session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM archived_sessions WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def _resurrect_sync(self, session_id: str, channel_id: str) -> GameState | None:
        """
        Copy an archived session back into the active sessions table under
        the given channel_id. Returns the loaded GameState, or None if the
        session_id was not found in the archive.
        Does NOT remove the archive entry — a copy stays in the archive.
        """
        row = self._conn.execute(
            "SELECT * FROM archived_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        # Restore under the requested channel_id (may differ from original)
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
            (channel_id, row["session_id"], row["dm_user_id"],
             _now_iso(), row["state_json"]),
        )
        self._conn.commit()
        return self._load_sync(channel_id)

    # ------------------------------------------------------------------
    # Public API — lock-guarded async versions
    # ------------------------------------------------------------------

    async def save_async(self, state: GameState) -> None:
        """Persist a GameState, serialising concurrent writes via the asyncio lock."""
        async with self._lock:
            self._save_sync(state)

    async def load_async(self, channel_id: str) -> GameState | None:
        async with self._lock:
            return self._load_sync(channel_id)

    async def delete_async(self, channel_id: str) -> None:
        async with self._lock:
            self._delete_sync(channel_id)

    async def list_channels_async(self) -> list[str]:
        async with self._lock:
            return self._list_sync()

    async def archive_async(self, channel_id: str, channel_name: str = "") -> bool:
        async with self._lock:
            return self._archive_sync(channel_id, channel_name)

    async def list_archive_async(self) -> list[dict]:
        async with self._lock:
            return self._list_archive_sync()

    async def load_archive_async(self, session_id: str) -> GameState | None:
        async with self._lock:
            return self._load_archive_sync(session_id)

    async def delete_archive_async(self, session_id: str) -> None:
        async with self._lock:
            self._delete_archive_sync(session_id)

    async def resurrect_async(self, session_id: str, channel_id: str) -> GameState | None:
        async with self._lock:
            return self._resurrect_sync(session_id, channel_id)

    # ------------------------------------------------------------------
    # Public API — sync convenience wrappers (safe for startup / tests)
    # ------------------------------------------------------------------

    def save(self, state: GameState) -> None:
        """
        Sync save. Safe to call at startup or from tests.
        In async contexts prefer save_async().
        """
        self._save_sync(state)

    def load(self, channel_id: str) -> GameState | None:
        return self._load_sync(channel_id)

    def delete(self, channel_id: str) -> None:
        self._delete_sync(channel_id)

    def list_channels(self) -> list[str]:
        return self._list_sync()

    def archive(self, channel_id: str, channel_name: str = "") -> bool:
        return self._archive_sync(channel_id, channel_name)

    def list_archive(self) -> list[dict]:
        return self._list_archive_sync()

    def load_archive(self, session_id: str) -> GameState | None:
        return self._load_archive_sync(session_id)

    def delete_archive(self, session_id: str) -> None:
        self._delete_archive_sync(session_id)

    def resurrect(self, session_id: str, channel_id: str) -> GameState | None:
        return self._resurrect_sync(session_id, channel_id)

    def close(self) -> None:
        self._conn.close()
