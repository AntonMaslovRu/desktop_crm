"""Догон брошенных корзин.

Корзина — заказ, который начали и не оплатили. Машина состояний:
    quarantine → ready → sent → replied
                └→ bought (купил сам)  └→ excluded (событие снято, поздно, опт)

Фильтры на входе: не больше четырёх билетов (больше — это опт, не письмо), не меньше
семи дней до события (позже с билетами не успеть), событие в трекинге. Один человек —
одно письмо: если ему уже писали по любой корзине, новую не догоняем.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg

from envo import db, letters, mailer
from envo.config import MSK
from envo.names import first_name
from envo.telegram import Telegram, rub

WINDOW_DAYS = 30
MAX_TICKETS = 4
MIN_DAYS_TO_EVENT = 7
QUARANTINE_HOURS = 3
MAX_PER_RUN = 10  # предохранитель: больше за раз — что-то не так


@dataclass
class CartStats:
    new: int = 0
    queued: int = 0
    bought: int = 0
    excluded: int = 0
    capped: bool = False
    ready_names: list[str] = field(default_factory=list)


def _set(conn: psycopg.Connection, order_id: int, state: str, reason: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO cart_followups (order_id, state, reason)
        VALUES (%s, %s, %s)
        ON CONFLICT (order_id) DO UPDATE
           SET state = EXCLUDED.state, reason = EXCLUDED.reason, updated_at = now()
        """,
        (order_id, state, reason),
    )


def scan(conn: psycopg.Connection, *, now: datetime | None = None) -> CartStats:
    """Разобрать корзины за окно: завести новые, закрыть отпавшие, поставить готовые в очередь."""
    now = now or datetime.now(MSK)
    stats = CartStats()
    since = now - timedelta(days=WINDOW_DAYS)

    carts = db.fetch_all(
        conn,
        """
        SELECT o.id, o.afisha_id, o.ordered_at, o.tickets_count, o.total, o.contact_id,
               c.email, c.name, c.salutation,
               e.display_name AS event, e.starts_at, e.tracking,
               f.state,
               EXISTS (SELECT 1 FROM orders p
                        WHERE p.contact_id = o.contact_id AND p.status = 'paid'
                          AND p.ordered_at > o.ordered_at) AS bought_later,
               EXISTS (SELECT 1 FROM letters l
                        WHERE l.kind = 'cart' AND l.to_addr = c.email
                          AND l.state IN ('queued', 'held', 'sent')) AS written_before
          FROM orders o
          LEFT JOIN contacts c ON c.id = o.contact_id
          LEFT JOIN events e ON e.id = o.event_id
          LEFT JOIN cart_followups f ON f.order_id = o.id
         WHERE o.status = 'cart' AND o.ordered_at >= %s
         ORDER BY o.total DESC
        """,
        (since,),
    )

    ready: list[dict] = []
    seen_emails: set[str] = set()
    for cart in carts:
        state = cart["state"]
        if state in ("sent", "replied", "bought", "excluded"):
            continue

        if cart["bought_later"]:
            _set(conn, cart["id"], "bought", "купил сам")
            stats.bought += 1
            continue

        days_left = (cart["starts_at"] - now.replace(tzinfo=None)).days if cart["starts_at"] else None
        dead = (
            cart["event"] is None or not cart["tracking"]
            or days_left is None or days_left < MIN_DAYS_TO_EVENT
        )
        if state is None:
            if dead or not cart["email"] or not (1 <= cart["tickets_count"] <= MAX_TICKETS):
                continue  # не заводим вовсе: нечего догонять
            _set(conn, cart["id"], "quarantine")
            stats.new += 1
            state = "quarantine"
        elif dead:
            _set(conn, cart["id"], "excluded", "событие снято или слишком близко")
            stats.excluded += 1
            continue

        aged = now - cart["ordered_at"]
        if aged < timedelta(hours=QUARANTINE_HOURS):
            continue
        if cart["written_before"] or cart["email"] in seen_emails:
            continue  # один человек — одно письмо
        seen_emails.add(cart["email"])
        ready.append(cart)

    if len(ready) > MAX_PER_RUN:
        stats.capped = True
        ready = ready[:MAX_PER_RUN]

    for cart in ready:
        name = cart["salutation"] or first_name(cart["name"])
        letter = letters.cart(name=name, tickets=cart["tickets_count"], event=cart["event"],
                              event_date=cart["starts_at"])
        if mailer.enqueue(conn, kind="cart", ref_id=cart["afisha_id"], contact_id=cart["contact_id"],
                          to=cart["email"], letter=letter):
            _set(conn, cart["id"], "ready")
            stats.queued += 1
            stats.ready_names.append(
                f"{name or 'без имени'} · {cart['event']} · {rub(float(cart['total']))}"
            )
    return stats


def mark_sent(conn: psycopg.Connection) -> int:
    """Письмо ушло из очереди → корзина в состоянии sent. Зовётся после дренажа очереди."""
    cursor = conn.execute(
        """
        WITH done AS (
            SELECT o.id, l.sent_at
              FROM cart_followups f
              JOIN orders o ON o.id = f.order_id
              JOIN letters l ON l.kind = 'cart' AND l.ref_id = o.afisha_id AND l.state = 'sent'
             WHERE f.state = 'ready'
        )
        UPDATE cart_followups f SET state = 'sent', letter_sent_at = done.sent_at, updated_at = now()
          FROM done WHERE f.order_id = done.id
        """,
    )
    return cursor.rowcount


def report(stats: CartStats, tg: Telegram) -> None:
    if not (stats.new or stats.queued or stats.bought or stats.excluded):
        return
    lines = ["<b>Брошенные корзины</b>"]
    if stats.new:
        lines.append(f"Новых в работе: <b>{stats.new}</b>")
    if stats.bought or stats.excluded:
        lines.append(f"Закрыто без письма: {stats.bought + stats.excluded}"
                     + (f" (купили сами: {stats.bought})" if stats.bought else ""))
    if stats.queued:
        lines.append(f"\n✉️ В очередь на отправку: <b>{stats.queued}</b>")
        lines += [f"  · {n}" for n in stats.ready_names]
    if stats.capped:
        lines.append(f"\n⚠️ Потолок {MAX_PER_RUN} писем за прогон, остальные — следующим разом.")
    tg.send("\n".join(lines))


def run(conn: psycopg.Connection, tg: Telegram, *, now: datetime | None = None) -> CartStats:
    with db.run_record(conn, "carts.scan") as slot:
        stats = scan(conn, now=now)
        sent = mark_sent(conn)
        slot.update({**stats.__dict__, "marked_sent": sent})
    report(stats, tg)
    return stats
