"""Сводки продаж владельцу: утренняя и вечерняя. Считаются из базы, не из API."""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from envo import db
from envo.config import MSK
from envo.telegram import Telegram, rub


def build(conn: psycopg.Connection, since: datetime, title: str, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    totals = db.fetch_one(
        conn,
        """
        SELECT count(*) FILTER (WHERE status = 'paid') AS orders,
               coalesce(sum(tickets_count) FILTER (WHERE status = 'paid'), 0) AS tickets,
               coalesce(sum(total) FILTER (WHERE status = 'paid'), 0) AS revenue,
               count(*) FILTER (WHERE status = 'refund') AS refunds,
               count(*) FILTER (WHERE status = 'cart') AS carts
          FROM orders
         WHERE ordered_at >= %s
        """,
        (since,),
    )
    by_event = db.fetch_all(
        conn,
        """
        SELECT coalesce(e.display_name, 'прочее') AS name, sum(o.tickets_count) AS n
          FROM orders o LEFT JOIN events e ON e.id = o.event_id
         WHERE o.status = 'paid' AND o.ordered_at >= %s
         GROUP BY 1 ORDER BY 2 DESC LIMIT 7
        """,
        (since,),
    )
    lines = [f"<b>{title}</b>", now.strftime("%d.%m.%Y, %H:%M") + " по Москве", ""]
    if totals["orders"]:
        lines.append(f"Билетов: <b>{totals['tickets']}</b> · заказов: <b>{totals['orders']}</b>"
                     f" · сумма: <b>{rub(float(totals['revenue']))}</b>")
    else:
        lines.append("Продаж за период нет.")
    if totals["refunds"]:
        lines.append(f"Возвратов: <b>{totals['refunds']}</b>")
    if totals["carts"]:
        lines.append(f"Брошенных корзин: {totals['carts']}")
    for row in by_event:
        lines.append(f"  {row['name']} — {row['n']}")
    return "\n".join(lines)


def morning(conn: psycopg.Connection, tg: Telegram, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    text = build(conn, now - timedelta(hours=14), "Продажи Envo · картина на утро", now)
    tg.send(text)
    return text


def evening(conn: psycopg.Connection, tg: Telegram, now: datetime | None = None) -> str:
    now = now or datetime.now(MSK)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = db.fetch_one(
        conn,
        "SELECT count(*) AS n FROM runs WHERE job = 'sales.sync' AND status = 'ok'"
        " AND started_at >= %s",
        (since,),
    )["n"]
    text = build(conn, since, "Продажи Envo · сводка за день", now)
    text += f"\n\n<i>Прогонов за день: {runs}</i>"
    tg.send(text)
    return text


def already_sent(conn: psycopg.Connection, key: str, day: str) -> bool:
    row = db.fetch_one(conn, "SELECT value FROM kv WHERE key = %s", (key,))
    return bool(row and row["value"] == day)


def mark_sent(conn: psycopg.Connection, key: str, day: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, day),
    )


def due(conn: psycopg.Connection, tg: Telegram, hours: tuple[int, ...] = (9, 19),
        now: datetime | None = None) -> list[str]:
    """Отправить сводки, время которых пришло и которые сегодня ещё не уходили.

    Пропущенная из-за простоя сводка уйдёт при первом же прогоне после — с пометкой.
    """
    now = now or datetime.now(MSK)
    day = now.strftime("%Y-%m-%d")
    sent: list[str] = []
    for hour in sorted(hours):
        if now.hour < hour:
            continue
        key = f"digest:{hour}"
        if already_sent(conn, key, day):
            continue
        text = morning(conn, tg, now) if hour == min(hours) else evening(conn, tg, now)
        if now.hour > hour:
            tg.send("<i>Сводка выше отправлена с опозданием.</i>")
        mark_sent(conn, key, day)
        sent.append(key)
    return sent
