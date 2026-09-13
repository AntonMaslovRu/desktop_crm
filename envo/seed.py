"""Начальные данные: справочник событий из Sheets и ступени. Повторный запуск безопасен."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from envo import db

SEED_DIR = Path(__file__).resolve().parent.parent / "deploy" / "seed"


def events(conn: psycopg.Connection, data: dict) -> int:
    """Отображаемые имена и тексты писем. Событие заводится заглушкой, если Афиша ещё не дала его."""
    n = 0
    for item in data["events"]:
        conn.execute(
            """
            INSERT INTO events (afisha_id, title, display_name, starts_at, tracking,
                                letter_single, letter_multi)
            VALUES (%(afisha_id)s, %(display_name)s, %(display_name)s, '1970-01-01', %(tracking)s,
                    %(letter_single)s, %(letter_multi)s)
            ON CONFLICT (afisha_id) DO UPDATE
               SET display_name = EXCLUDED.display_name,
                   tracking = EXCLUDED.tracking,
                   letter_single = COALESCE(EXCLUDED.letter_single, events.letter_single),
                   letter_multi = COALESCE(EXCLUDED.letter_multi, events.letter_multi),
                   updated_at = now()
            """,
            item,
        )
        conn.execute(
            """
            INSERT INTO event_facts (event_id, field, value, source)
            SELECT id, 'display_name', %s, 'manual' FROM events WHERE afisha_id = %s
            ON CONFLICT (event_id, field) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (item["display_name"], item["afisha_id"]),
        )
        n += 1
    return n


def ladders(conn: psycopg.Connection, data: dict) -> int:
    n = 0
    for item in data["ladders"]:
        event = db.fetch_one(conn, "SELECT id FROM events WHERE afisha_id = %s", (item["afisha_id"],))
        if event is None:
            raise RuntimeError(f"событие {item['afisha_id']} ещё не в базе: сначала envoctl sync")
        cat = db.fetch_one(
            conn,
            "INSERT INTO categories (event_id, name) VALUES (%s, %s)"
            " ON CONFLICT (event_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (event["id"], item["category"]),
        )["id"]
        for alias in item["aliases"]:
            conn.execute(
                "INSERT INTO category_aliases (category_id, alias) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING", (cat, alias),
            )
        for idx, step in enumerate(item["steps"], 1):
            conn.execute(
                """
                INSERT INTO steps (category_id, idx, price, quota, opened_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (category_id, idx) DO UPDATE
                   SET price = EXCLUDED.price, quota = EXCLUDED.quota,
                       opened_at = COALESCE(steps.opened_at, EXCLUDED.opened_at)
                """,
                (cat, idx, step["price"], step["quota"],
                 item.get("count_from") if idx == 1 else None),
            )
        # билеты, пришедшие до сида, получают категорию задним числом
        conn.execute(
            """
            UPDATE tickets t SET category_id = %s
              FROM orders o, category_aliases a
             WHERE t.order_id = o.id AND o.event_id = %s AND t.category_id IS NULL
               AND a.category_id = %s AND position(lower(a.alias) in lower(t.sector)) > 0
            """,
            (cat, event["id"], cat),
        )
        n += 1
    return n


def run(conn: psycopg.Connection, seed_dir: Path = SEED_DIR) -> dict:
    result = {}
    ref = seed_dir / "reference.json"
    if ref.exists():
        result["events"] = events(conn, json.loads(ref.read_text()))
    lad = seed_dir / "ladders.json"
    if lad.exists():
        result["ladders"] = ladders(conn, json.loads(lad.read_text()))
    db.audit(conn, "система", "сид", result)
    return result
