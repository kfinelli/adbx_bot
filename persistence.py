"""
persistence.py — SQLite-backed save/load for GameState and Characters.

Database schema:

    sessions
    --------
    channel_id   TEXT PRIMARY KEY   -- Discord channel ID
    session_id   TEXT               -- GameState.session_id (UUID string)
    dm_user_id   TEXT               -- Discord user ID of DM
    updated_at   TEXT               -- ISO 8601 timestamp of last save
    state_json   TEXT               -- Full serialized GameState (minus characters)

    characters
    ----------
    character_id  TEXT PRIMARY KEY  -- UUID string
    owner_id      TEXT              -- Discord user ID of owner
    name          TEXT              -- Character name
    character_class TEXT            -- Class enum value
    level         INTEGER           -- Character level
    experience    INTEGER           -- XP total
    ability_scores_json TEXT        -- JSON blob for AzureStats
    hp_max        INTEGER           -- Max HP
    hp_current    INTEGER           -- Current HP
    armor_class   INTEGER           -- AC
    movement_speed INTEGER          -- Movement speed
    saving_throws_json TEXT         -- JSON blob for saving throws dict
    status        TEXT              -- CharacterStatus enum value
    status_notes  TEXT              -- Status notes
    inventory_json TEXT             -- JSON array of InventoryItem dicts
    gold          INTEGER           -- Gold pieces
    spellbook_json TEXT             -- JSON blob for SpellBook or NULL
    created_at    TEXT              -- ISO 8601 timestamp
    is_pregenerated INTEGER         -- 0 or 1
    updated_at    TEXT              -- ISO 8601 timestamp of last update

    session_characters
    ------------------
    session_id    TEXT              -- GameState.session_id UUID
    character_id  TEXT              -- Character.character_id UUID
    joined_at     TEXT              -- ISO 8601 timestamp when enrolled
    PRIMARY KEY (session_id, character_id)

    archived_sessions
    -----------------
    session_id   TEXT PRIMARY KEY  -- GameState.session_id UUID
    channel_id   TEXT NOT NULL     -- original channel
    channel_name TEXT              -- human-readable name at archive time
    dm_user_id   TEXT
    turn_number  INTEGER NOT NULL DEFAULT 0
    created_at   TEXT
    updated_at   TEXT NOT NULL
    archived_at  TEXT NOT NULL
    state_json   TEXT NOT NULL

Usage:
    db = Database("dungeon.db")
    db.save(state)
    state = db.load(channel_id)
    db.save_character(char)
    char = db.load_character(character_id)
    all_ids = db.list_channels()
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

from models import GameState
from serialization import deserialize_state


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
            CREATE TABLE IF NOT EXISTS characters (
                character_id      TEXT PRIMARY KEY,
                owner_id          TEXT,
                name              TEXT NOT NULL,
                character_class   TEXT NOT NULL,
                level             INTEGER NOT NULL DEFAULT 1,
                experience        INTEGER NOT NULL DEFAULT 0,
                ability_scores_json TEXT NOT NULL,
                hp_max            INTEGER NOT NULL,
                hp_current        INTEGER NOT NULL,
                armor_class       INTEGER NOT NULL,
                movement_speed    INTEGER NOT NULL,
                saving_throws_json TEXT NOT NULL,
                status            TEXT NOT NULL,
                status_notes      TEXT NOT NULL DEFAULT '',
                inventory_json    TEXT NOT NULL,
                gold              INTEGER NOT NULL DEFAULT 0,
                spellbook_json    TEXT,
                created_at        TEXT NOT NULL,
                is_pregenerated   INTEGER NOT NULL DEFAULT 0,
                updated_at        TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS session_characters (
                session_id    TEXT NOT NULL,
                character_id  TEXT NOT NULL,
                joined_at     TEXT NOT NULL,
                PRIMARY KEY (session_id, character_id),
                FOREIGN KEY (character_id) REFERENCES characters(character_id)
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
        """Save session state (without characters) to the sessions table.

        Characters are saved separately via save_character() and linked via
        session_characters join table.
        """
        from serialization import serialize_state_without_characters

        # Save each character to the characters table
        for char in state.characters.values():
            self._save_character_sync(char)
            # Link character to this session
            self._enroll_character_in_session_sync(str(state.session_id), str(char.character_id))

        # Save session state without character data
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
                serialize_state_without_characters(state),
            ),
        )
        self._conn.commit()

    def _load_sync(self, channel_id: str) -> GameState | None:
        """Load session state and populate characters from the characters table."""
        row = self._conn.execute(
            "SELECT state_json, session_id FROM sessions WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None

        # Load characters enrolled in this session
        session_id = row["session_id"]
        characters = self._get_characters_for_session_sync(session_id)

        # Deserialize state with loaded characters
        return deserialize_state(row["state_json"], characters=characters)

    def _delete_sync(self, channel_id: str) -> None:
        self._conn.execute(
            "DELETE FROM sessions WHERE channel_id = ?",
            (channel_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Character persistence
    # ------------------------------------------------------------------

    def _save_character_sync(self, character) -> None:
        """Save or update a single Character to the characters table."""
        from serialization import (
            serialize_character,
        )
        char_data = serialize_character(character)
        self._conn.execute(
            """
            INSERT INTO characters (
                character_id, owner_id, name, character_class, level,
                experience, ability_scores_json, hp_max, hp_current,
                armor_class, movement_speed, saving_throws_json, status,
                status_notes, inventory_json, gold, spellbook_json,
                created_at, is_pregenerated, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                name = excluded.name,
                character_class = excluded.character_class,
                level = excluded.level,
                experience = excluded.experience,
                ability_scores_json = excluded.ability_scores_json,
                hp_max = excluded.hp_max,
                hp_current = excluded.hp_current,
                armor_class = excluded.armor_class,
                movement_speed = excluded.movement_speed,
                saving_throws_json = excluded.saving_throws_json,
                status = excluded.status,
                status_notes = excluded.status_notes,
                inventory_json = excluded.inventory_json,
                gold = excluded.gold,
                spellbook_json = excluded.spellbook_json,
                is_pregenerated = excluded.is_pregenerated,
                updated_at = excluded.updated_at
            """,
            (
                char_data["character_id"],
                char_data["owner_id"],
                char_data["name"],
                char_data["character_class"],
                char_data["level"],
                char_data["experience"],
                json.dumps(char_data["ability_scores"]),
                char_data["hp_max"],
                char_data["hp_current"],
                char_data["armor_class"],
                char_data["movement_speed"],
                json.dumps(char_data["saving_throws"]),
                char_data["status"],
                char_data["status_notes"],
                json.dumps(char_data["inventory"]),
                char_data["gold"],
                json.dumps(char_data["spellbook"]) if char_data["spellbook"] else None,
                char_data["created_at"],
                1 if char_data["is_pregenerated"] else 0,
                _now_iso(),
            ),
        )
        self._conn.commit()

    def _load_character_sync(self, character_id: str):
        """Load a single Character by UUID string. Returns Character or None."""
        from serialization import (
            deserialize_character,
        )
        row = self._conn.execute(
            "SELECT * FROM characters WHERE character_id = ?",
            (character_id,),
        ).fetchone()
        if row is None:
            return None
        # Reconstruct the dict format expected by deserialize_character
        char_dict = {
            "character_id": row["character_id"],
            "owner_id": row["owner_id"],
            "name": row["name"],
            "character_class": row["character_class"],
            "level": row["level"],
            "experience": row["experience"],
            "ability_scores": json.loads(row["ability_scores_json"]),
            "hp_max": row["hp_max"],
            "hp_current": row["hp_current"],
            "armor_class": row["armor_class"],
            "movement_speed": row["movement_speed"],
            "saving_throws": json.loads(row["saving_throws_json"]),
            "status": row["status"],
            "status_notes": row["status_notes"],
            "inventory": json.loads(row["inventory_json"]),
            "gold": row["gold"],
            "spellbook": json.loads(row["spellbook_json"]) if row["spellbook_json"] else None,
            "created_at": row["created_at"],
            "is_pregenerated": bool(row["is_pregenerated"]),
        }
        return deserialize_character(char_dict)

    def _delete_character_sync(self, character_id: str) -> None:
        """Delete a character from the characters table."""
        self._conn.execute(
            "DELETE FROM characters WHERE character_id = ?",
            (character_id,),
        )
        self._conn.commit()

    def _enroll_character_in_session_sync(self, session_id: str, character_id: str) -> None:
        """Link a character to a session via session_characters join table."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO session_characters (session_id, character_id, joined_at)
            VALUES (?, ?, ?)
            """,
            (session_id, character_id, _now_iso()),
        )
        self._conn.commit()

    def _unenroll_character_from_session_sync(self, session_id: str, character_id: str) -> None:
        """Remove a character-session link."""
        self._conn.execute(
            "DELETE FROM session_characters WHERE session_id = ? AND character_id = ?",
            (session_id, character_id),
        )
        self._conn.commit()

    def _get_characters_for_session_sync(self, session_id: str) -> dict:
        """Load all characters enrolled in a session as a dict keyed by UUID."""
        rows = self._conn.execute(
            """
            SELECT c.* FROM characters c
            JOIN session_characters sc ON c.character_id = sc.character_id
            WHERE sc.session_id = ?
            """,
            (session_id,),
        ).fetchall()
        from serialization import deserialize_character
        characters = {}
        for row in rows:
            char_dict = {
                "character_id": row["character_id"],
                "owner_id": row["owner_id"],
                "name": row["name"],
                "character_class": row["character_class"],
                "level": row["level"],
                "experience": row["experience"],
                "ability_scores": json.loads(row["ability_scores_json"]),
                "hp_max": row["hp_max"],
                "hp_current": row["hp_current"],
                "armor_class": row["armor_class"],
                "movement_speed": row["movement_speed"],
                "saving_throws": json.loads(row["saving_throws_json"]),
                "status": row["status"],
                "status_notes": row["status_notes"],
                "inventory": json.loads(row["inventory_json"]),
                "gold": row["gold"],
                "spellbook": json.loads(row["spellbook_json"]) if row["spellbook_json"] else None,
                "created_at": row["created_at"],
                "is_pregenerated": bool(row["is_pregenerated"]),
            }
            char = deserialize_character(char_dict)
            characters[char.character_id] = char
        return characters

    def _list_all_characters_sync(self) -> list:
        """Return a list of all character IDs in the database."""
        rows = self._conn.execute(
            "SELECT character_id FROM characters ORDER BY updated_at DESC"
        ).fetchall()
        return [row["character_id"] for row in rows]

    def _get_characters_by_owner_sync(self, owner_id: str) -> list:
        """Return a list of Character objects owned by the given Discord user
        ID."""
        from serialization import deserialize_character
        rows = self._conn.execute(
            """
            SELECT * FROM characters
            WHERE owner_id = ?
            ORDER BY updated_at DESC
            """,
            (owner_id,),
        ).fetchall()
        characters = []
        for row in rows:
            char_dict = {
                "character_id": row["character_id"],
                "owner_id": row["owner_id"],
                "name": row["name"],
                "character_class": row["character_class"],
                "level": row["level"],
                "experience": row["experience"],
                "ability_scores": json.loads(row["ability_scores_json"]),
                "hp_max": row["hp_max"],
                "hp_current": row["hp_current"],
                "armor_class": row["armor_class"],
                "movement_speed": row["movement_speed"],
                "saving_throws": json.loads(row["saving_throws_json"]),
                "status": row["status"],
                "status_notes": row["status_notes"],
                "inventory": json.loads(row["inventory_json"]),
                "gold": row["gold"],
                "spellbook": json.loads(row["spellbook_json"]) if row["spellbook_json"] else None,
                "created_at": row["created_at"],
                "is_pregenerated": bool(row["is_pregenerated"]),
            }
            char = deserialize_character(char_dict)
            characters.append(char)
        return characters

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

        Note: Characters are NOT deleted when archiving — they persist in
        the characters table and can be enrolled in new sessions later.
        Only the session_characters links are removed when the session is deleted.
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
        # Delete session-character links but keep characters
        session_id = row["session_id"]
        self._conn.execute(
            "DELETE FROM session_characters WHERE session_id = ?",
            (session_id,),
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
    # Character persistence — async wrappers
    # ------------------------------------------------------------------

    async def save_character_async(self, character) -> None:
        """Persist a single Character."""
        async with self._lock:
            self._save_character_sync(character)

    async def load_character_async(self, character_id: str):
        """Load a single Character by UUID string."""
        async with self._lock:
            return self._load_character_sync(character_id)

    async def delete_character_async(self, character_id: str) -> None:
        """Delete a Character from the database."""
        async with self._lock:
            self._delete_character_sync(character_id)

    async def enroll_character_in_session_async(self, session_id: str, character_id: str) -> None:
        """Link a character to a session."""
        async with self._lock:
            self._enroll_character_in_session_sync(session_id, character_id)

    async def unenroll_character_from_session_async(self, session_id: str, character_id: str) -> None:
        """Remove a character-session link."""
        async with self._lock:
            self._unenroll_character_from_session_sync(session_id, character_id)

    async def get_characters_for_session_async(self, session_id: str) -> dict:
        """Load all characters enrolled in a session."""
        async with self._lock:
            return self._get_characters_for_session_sync(session_id)

    async def list_all_characters_async(self) -> list:
        """Return a list of all character IDs in the database."""
        async with self._lock:
            return self._list_all_characters_sync()

    async def get_characters_by_owner_async(self, owner_id: str) -> list:
        """Return a list of Character objects owned by the given Discord user
        ID."""
        async with self._lock:
            return self._get_characters_by_owner_sync(owner_id)

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

    # Character persistence — sync wrappers
    def save_character(self, character) -> None:
        """Sync save for a single Character."""
        self._save_character_sync(character)

    def load_character(self, character_id: str):
        """Sync load for a single Character."""
        return self._load_character_sync(character_id)

    def delete_character(self, character_id: str) -> None:
        """Sync delete for a Character."""
        self._delete_character_sync(character_id)

    def enroll_character_in_session(self, session_id: str, character_id: str) -> None:
        """Sync enroll a character in a session."""
        self._enroll_character_in_session_sync(session_id, character_id)

    def unenroll_character_from_session(self, session_id: str, character_id: str) -> None:
        """Sync unenroll a character from a session."""
        self._unenroll_character_from_session_sync(session_id, character_id)

    def get_characters_for_session(self, session_id: str) -> dict:
        """Sync load all characters for a session."""
        return self._get_characters_for_session_sync(session_id)

    def list_all_characters(self) -> list:
        """Sync list all character IDs."""
        return self._list_all_characters_sync()

    def get_characters_by_owner(self, owner_id: str) -> list:
        """Sync list all Character objects owned by the given Discord user
        ID."""
        return self._get_characters_by_owner_sync(owner_id)

    def close(self) -> None:
        self._conn.close()
