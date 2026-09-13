"""Правило «заказ оплачен → вэлком».

Подписано на шину: берёт непрочитанные order.created и order.paid, ставит письмо
в очередь, если у заказа есть адрес и у события есть инструкция. Без инструкции —
в «Требует внимания», а не молчаливый пропуск.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from envo import db, letters, mailer
from envo.names import first_name


@dataclass
class WelcomeStats:
    queued: int = 0
    no_email: int = 0
    no_instructions: list[str] = field(default_factory=list)
    already: int = 0


def process(conn: psycopg.Connection, *, hold: bool = False) -> WelcomeStats:
    stats = WelcomeStats()
    pending = db.fetch_all(
        conn,
        """
        SELECT l.id AS log_id, l.payload->>'order' AS afisha_id
          FROM events_log l
         WHERE l.topic IN ('order.created', 'order.paid') AND l.processed_at IS NULL
         ORDER BY l.id
        """,
    )
    for item in pending:
        conn.execute("UPDATE events_log SET processed_at = now() WHERE id = %s", (item["log_id"],))
        order = db.fetch_one(
            conn,
            """
            SELECT o.afisha_id, o.status, o.contact_id, o.tickets_count, o.ordered_at,
                   c.email, c.name, c.salutation,
                   e.display_name AS event, e.starts_at, e.letter_single, e.letter_multi
              FROM orders o
              LEFT JOIN contacts c ON c.id = o.contact_id
              LEFT JOIN events e ON e.id = o.event_id
             WHERE o.afisha_id = %s
            """,
            (item["afisha_id"],),
        )
        if order is None or order["status"] != "paid":
            continue
        if not order["email"]:
            stats.no_email += 1
            continue
        instructions = (order["letter_multi"] if order["tickets_count"] >= 2 else None) \
            or order["letter_single"]
        if not instructions:
            stats.no_instructions.append(order["event"] or item["afisha_id"])
            continue
        letter = letters.welcome(
            name=order["salutation"] or first_name(order["name"]),
            tickets=order["tickets_count"],
            event=order["event"] or "событие",
            event_date=order["starts_at"],
            order_id=order["afisha_id"],
            instructions=instructions,
        )
        if mailer.enqueue(conn, kind="welcome", ref_id=order["afisha_id"],
                          contact_id=order["contact_id"], to=order["email"], letter=letter,
                          hold=hold):
            stats.queued += 1
        else:
            stats.already += 1
    return stats
