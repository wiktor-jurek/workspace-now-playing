"""SQLite storage. Refresh tokens are encrypted at rest with a key derived from SECRET_KEY."""

import base64
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from . import config

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SECRET_KEY.encode()).digest()))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    google_sub TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    google_refresh_token TEXT,
    spotify_refresh_token TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_status TEXT,
    last_error TEXT,
    created_at INTEGER NOT NULL
);
"""

_conn: sqlite3.Connection | None = None


@dataclass
class User:
    id: int
    email: str
    google_refresh_token: str | None
    spotify_refresh_token: str | None
    enabled: bool
    last_status: str | None
    last_error: str | None


def _encrypt(value: str | None) -> str | None:
    return _fernet.encrypt(value.encode()).decode() if value else None


def _decrypt(value: str | None) -> str | None:
    return _fernet.decrypt(value.encode()).decode() if value else None


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        google_refresh_token=_decrypt(row["google_refresh_token"]),
        spotify_refresh_token=_decrypt(row["spotify_refresh_token"]),
        enabled=bool(row["enabled"]),
        last_status=row["last_status"],
        last_error=row["last_error"],
    )


def connect() -> None:
    global _conn
    Path(config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA)


def close() -> None:
    if _conn:
        _conn.close()


def _db() -> sqlite3.Connection:
    assert _conn is not None, "db.connect() not called"
    return _conn


def upsert_google_user(sub: str, email: str, refresh_token: str | None) -> int:
    """Create or update a user; keeps the stored refresh token if Google didn't send a new one."""
    _db().execute(
        """
        INSERT INTO users (google_sub, email, google_refresh_token, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(google_sub) DO UPDATE SET
            email = excluded.email,
            google_refresh_token = COALESCE(excluded.google_refresh_token, google_refresh_token)
        """,
        (sub, email, _encrypt(refresh_token), int(time.time())),
    )
    return _db().execute("SELECT id FROM users WHERE google_sub = ?", (sub,)).fetchone()["id"]


def get_user(user_id: int) -> User | None:
    row = _db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user(row) if row else None


def active_users() -> list[User]:
    rows = _db().execute(
        "SELECT * FROM users WHERE enabled = 1"
        " AND google_refresh_token IS NOT NULL AND spotify_refresh_token IS NOT NULL"
    ).fetchall()
    return [_user(r) for r in rows]


def set_google_token(user_id: int, refresh_token: str | None) -> None:
    _db().execute("UPDATE users SET google_refresh_token = ? WHERE id = ?", (_encrypt(refresh_token), user_id))


def set_spotify_token(user_id: int, refresh_token: str | None) -> None:
    _db().execute("UPDATE users SET spotify_refresh_token = ? WHERE id = ?", (_encrypt(refresh_token), user_id))


def set_enabled(user_id: int, enabled: bool) -> None:
    _db().execute("UPDATE users SET enabled = ? WHERE id = ?", (int(enabled), user_id))


def set_status(user_id: int, status: str | None) -> None:
    _db().execute("UPDATE users SET last_status = ?, last_error = NULL WHERE id = ?", (status, user_id))


def set_error(user_id: int, error: str | None) -> None:
    _db().execute("UPDATE users SET last_error = ? WHERE id = ?", (error, user_id))


def delete_user(user_id: int) -> None:
    _db().execute("DELETE FROM users WHERE id = ?", (user_id,))
