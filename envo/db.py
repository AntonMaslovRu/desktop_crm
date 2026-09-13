"""Доступ к базе. Один модуль знает про SQL, остальные работают через репозитории."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def dsn() -> str:
    value = os.environ.get("ENVO_DB_DSN")
    if not value:
        raise RuntimeError("ENVO_DB_DSN не задан")
    return value


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Соединение с транзакцией: успех — коммит, исключение — откат."""
    with psycopg.connect(url or dsn(), row_factory=dict_row) as conn:
        yield conn


def apply_schema(conn: psycopg.Connection, path: Path = SCHEMA) -> None:
    conn.execute(path.read_text())


def fetch_one(conn: psycopg.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def fetch_all(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return conn.execute(sql, params).fetchall()


def publish(conn: psycopg.Connection, topic: str, payload: dict) -> int:
    """Сообщение в шину. Модули не зовут друг друга — они публикуют события."""
    import json

    row = conn.execute(
        "INSERT INTO events_log (topic, payload) VALUES (%s, %s) RETURNING id",
        (topic, json.dumps(payload, ensure_ascii=False, default=str)),
    ).fetchone()
    return row["id"]


def audit(conn: psycopg.Connection, actor: str, action: str, payload: dict | None = None) -> None:
    import json

    conn.execute(
        "INSERT INTO audit (actor, action, payload) VALUES (%s, %s, %s)",
        (actor, action, json.dumps(payload or {}, ensure_ascii=False, default=str)),
    )


@contextmanager
def run_record(conn: psycopg.Connection, job: str) -> Iterator[dict]:
    """Запись о прогоне: старт, итог, ошибка. Из неё живёт «Журнал прогонов»."""
    import json

    row = conn.execute(
        "INSERT INTO runs (job, started_at) VALUES (%s, clock_timestamp()) RETURNING id", (job,)
    ).fetchone()
    stats: dict[str, Any] = {}
    try:
        yield stats
    except Exception as exc:
        conn.execute(
            "UPDATE runs SET finished_at = clock_timestamp(), status = 'failed',"
            " error = %s, stats = %s"
            " WHERE id = %s",
            (str(exc)[:2000], json.dumps(stats, ensure_ascii=False, default=str), row["id"]),
        )
        raise
    else:
        conn.execute(
            "UPDATE runs SET finished_at = clock_timestamp(), status = 'ok', stats = %s"
            " WHERE id = %s",
            (json.dumps(stats, ensure_ascii=False, default=str), row["id"]),
        )


def single_run(conn: psycopg.Connection, job: str) -> bool:
    """Блокировка «одна копия задачи»: ручной запуск не пересечётся с расписанием.

    Блокировка держится до конца транзакции, снимать руками не нужно.
    """
    import zlib

    key = zlib.crc32(job.encode()) & 0x7FFFFFFF  # hash() у Python случайный на процесс
    row = conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS got", (key,)).fetchone()
    return bool(row["got"])
