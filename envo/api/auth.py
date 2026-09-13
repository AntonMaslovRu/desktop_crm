"""Вход по логину и паролю. Пароль — argon2id, токен — случайный, в базе только его хеш."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from envo import db

SESSION_DAYS = 30
MAX_FAILS = 5  # подряд неудачных попыток → пауза
LOCK_MINUTES = 15

_hasher = PasswordHasher()
_fails: dict[str, list[datetime]] = {}


def create_user(conn: psycopg.Connection, login: str, password: str) -> int:
    if len(password) < 10:
        raise ValueError("пароль короче 10 символов")
    row = db.fetch_one(
        conn,
        "INSERT INTO users (login, password_hash) VALUES (%s, %s)"
        " ON CONFLICT (login) DO UPDATE SET password_hash = EXCLUDED.password_hash RETURNING id",
        (login.strip().lower(), _hasher.hash(password)),
    )
    db.audit(conn, login, "пароль задан")
    return row["id"]


def _locked(login: str, now: datetime) -> bool:
    recent = [t for t in _fails.get(login, []) if now - t < timedelta(minutes=LOCK_MINUTES)]
    _fails[login] = recent
    return len(recent) >= MAX_FAILS


def login(conn: psycopg.Connection, login: str, password: str, *, device: str = "",
          now: datetime | None = None) -> str | None:
    """Возвращает токен сессии или None. Пять промахов подряд — пауза на четверть часа."""
    now = now or datetime.now(timezone.utc)
    login = login.strip().lower()
    if _locked(login, now):
        return None
    user = db.fetch_one(conn, "SELECT id, password_hash FROM users WHERE login = %s", (login,))
    try:
        if user is None:
            raise VerifyMismatchError
        _hasher.verify(user["password_hash"], password)
    except VerifyMismatchError:
        _fails.setdefault(login, []).append(now)
        db.audit(conn, login, "неудачный вход")
        return None
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, device) VALUES (%s, %s, %s, %s)",
        (_hash(token), user["id"], now + timedelta(days=SESSION_DAYS), device[:200]),
    )
    conn.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (now, user["id"]))
    _fails.pop(login, None)
    return token


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def whoami(conn: psycopg.Connection, token: str, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    row = db.fetch_one(
        conn,
        """
        UPDATE sessions s SET last_seen_at = %s
          FROM users u
         WHERE s.token_hash = %s AND s.user_id = u.id AND s.expires_at > %s
        RETURNING u.login
        """,
        (now, _hash(token), now),
    )
    return row["login"] if row else None


def logout(conn: psycopg.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash(token),))
