"""Заказы и билеты из Афиши в базу.

Правило одно: повторный прогон ничего не дублирует и ничего не теряет. Окно берём с
запасом, потому что пропущенный прогон должен догоняться следующим, а не оператором.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from envo import db
from envo.afisha import AfishaClient, Order
from envo.names import first_name
from envo.normalize import normalize_email, normalize_phone

WINDOW_DAYS = 3  # окно сканирования продаж; корзины смотрят глубже
SYSTEM = "система"

STATUS_BY_ORDER = {True: "возврат", False: "оплачен"}


@dataclass
class IngestStats:
    orders_new: int = 0
    orders_updated: int = 0
    tickets_new: int = 0
    returns: int = 0
    contacts_new: int = 0
    contacts_merged: int = 0
    unknown_events: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "заказов новых": self.orders_new,
            "заказов обновлено": self.orders_updated,
            "билетов новых": self.tickets_new,
            "возвратов": self.returns,
            "клиентов новых": self.contacts_new,
            "склеек": self.contacts_merged,
            "событий вне каталога": len(self.unknown_events),
        }


def order_status(order: Order) -> str:
    if order.is_returned:
        return "возврат"
    return "оплачен" if order.status == 1 else "корзина"


def upsert_contact(conn: psycopg.Connection, order: Order, stats: IngestStats) -> int | None:
    """Находит или заводит клиента. Склейка автоматическая по почте или телефону."""
    email = normalize_email(order.email)
    phone = normalize_phone(order.phone)
    if not email and not phone:
        return None

    found = db.fetch_all(
        conn,
        """
        SELECT id, email, phone FROM contacts
         WHERE merged_into IS NULL
           AND ((%s <> '' AND email = %s) OR (%s <> '' AND phone = %s))
         ORDER BY id
        """,
        (email, email, phone, phone),
    )

    if not found:
        row = db.fetch_one(
            conn,
            "INSERT INTO contacts (email, phone, name, salutation)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (email or None, phone or None, order.customer_name or None,
             first_name(order.customer_name) or None),
        )
        stats.contacts_new += 1
        remember_contacts(conn, row["id"], email, phone)
        return row["id"]

    keeper = found[0]["id"]
    # Один человек с двух адресов — склеиваем, старую карточку помечаем ссылкой на живую.
    for extra in found[1:]:
        conn.execute("UPDATE contacts SET merged_into = %s WHERE id = %s", (keeper, extra["id"]))
        conn.execute("UPDATE orders SET contact_id = %s WHERE contact_id = %s",
                     (keeper, extra["id"]))
        for table, column in (("contact_emails", "email"), ("contact_phones", "phone")):
            conn.execute(
                f"INSERT INTO {table} (contact_id, {column})"
                f" SELECT %s, {column} FROM {table} WHERE contact_id = %s"
                f" ON CONFLICT (contact_id, {column}) DO NOTHING",
                (keeper, extra["id"]),
            )
        db.audit(conn, SYSTEM, "склейка клиентов", {"оставлен": keeper, "скрыт": extra["id"]})
        stats.contacts_merged += 1

    # Основным становится самый свежий адрес: человек пишет с того, с которого заказал.
    conn.execute(
        """
        UPDATE contacts
           SET email = COALESCE(NULLIF(%s, ''), email),
               phone = COALESCE(NULLIF(%s, ''), phone),
               name  = COALESCE(name, %s),
               salutation = COALESCE(salutation, %s)
         WHERE id = %s
        """,
        (email, phone, order.customer_name or None,
         first_name(order.customer_name) or None, keeper),
    )
    remember_contacts(conn, keeper, email, phone)
    return keeper


def remember_contacts(conn: psycopg.Connection, contact_id: int, email: str, phone: str) -> None:
    """Копим все адреса и телефоны человека: по ним ищется переписка и ловятся дубли."""
    if email:
        conn.execute(
            "INSERT INTO contact_emails (contact_id, email) VALUES (%s, %s)"
            " ON CONFLICT (contact_id, email) DO UPDATE SET last_seen_at = now()",
            (contact_id, email),
        )
    if phone:
        conn.execute(
            "INSERT INTO contact_phones (contact_id, phone) VALUES (%s, %s)"
            " ON CONFLICT (contact_id, phone) DO UPDATE SET last_seen_at = now()",
            (contact_id, phone),
        )


def event_id_for(conn: psycopg.Connection, afisha_event_id: int) -> int | None:
    row = db.fetch_one(conn, "SELECT id FROM events WHERE afisha_id = %s", (afisha_event_id,))
    return row["id"] if row else None


def category_for(conn: psycopg.Connection, event_id: int, sector: str) -> int | None:
    """Сектор → категория по алиасам. Незнакомый сектор не угадывается."""
    if not sector:
        return None
    row = db.fetch_one(
        conn,
        """
        SELECT c.id FROM categories c
          JOIN category_aliases a ON a.category_id = c.id
         WHERE c.event_id = %s AND position(lower(a.alias) in lower(%s)) > 0
         ORDER BY length(a.alias) DESC
         LIMIT 1
        """,
        (event_id, sector),
    )
    return row["id"] if row else None


def sync_orders(
    conn: psycopg.Connection,
    client: AfishaClient,
    *,
    today: date | None = None,
    window_days: int = WINDOW_DAYS,
) -> IngestStats:
    """Один прогон синхронизации. Идемпотентен: гонять можно сколько угодно."""
    stats = IngestStats()
    today = today or date.today()
    end = today + timedelta(days=1)
    start = end - timedelta(days=window_days + 1)

    for order in client.orders(start, end):
        status = order_status(order)
        contact_id = upsert_contact(conn, order, stats)

        tickets = client.tickets(order.id)
        afisha_event = next((t.event_id for t in tickets if t.event_id), None)
        event_id = event_id_for(conn, afisha_event) if afisha_event else None
        if afisha_event and event_id is None:
            stats.unknown_events.append(str(afisha_event))

        existing = db.fetch_one(
            conn, "SELECT id, status FROM orders WHERE afisha_id = %s", (order.id,)
        )
        row = db.fetch_one(
            conn,
            """
            INSERT INTO orders (afisha_id, event_id, contact_id, status, ordered_at,
                                total, tickets_count, raw, channel)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Афиша')
            ON CONFLICT (afisha_id) DO UPDATE
               SET status = EXCLUDED.status,
                   event_id = COALESCE(orders.event_id, EXCLUDED.event_id),
                   contact_id = COALESCE(orders.contact_id, EXCLUDED.contact_id),
                   total = EXCLUDED.total,
                   tickets_count = EXCLUDED.tickets_count,
                   raw = EXCLUDED.raw,
                   last_seen_at = now()
            RETURNING id
            """,
            (order.id, event_id, contact_id, status, order.ordered_at, order.total,
             order.tickets_count, psycopg.types.json.Json(order.raw), ),
        )
        order_pk = row["id"]

        if existing is None:
            stats.orders_new += 1
            db.publish(conn, "order.created", {"order": order.id, "status": status,
                                               "event": event_id, "total": float(order.total)})
        else:
            stats.orders_updated += 1
            if existing["status"] != "возврат" and status == "возврат":
                stats.returns += 1
                db.publish(conn, "order.returned", {"order": order.id, "event": event_id})

        for ticket in tickets:
            inserted = db.fetch_one(
                conn,
                """
                INSERT INTO tickets (order_id, afisha_id, category_id, sector, barcode,
                                     price, status, sold_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id, afisha_id) DO UPDATE
                   SET price = EXCLUDED.price, status = EXCLUDED.status
                RETURNING (xmax = 0) AS is_new
                """,
                (order_pk, ticket.id,
                 category_for(conn, event_id, ticket.sector) if event_id else None,
                 ticket.sector, ticket.barcode, ticket.price,
                 "возврат" if order.is_returned else "продан", order.ordered_at),
            )
            if inserted and inserted["is_new"]:
                stats.tickets_new += 1

    return stats


def run(conn: psycopg.Connection, client: AfishaClient, **kw) -> IngestStats:
    """Прогон с записью в журнал и блокировкой «одна копия»."""
    if not db.single_run(conn, "sales.sync"):
        raise RuntimeError("продажи уже синхронизируются другим процессом")
    with db.run_record(conn, "sales.sync") as stats_slot:
        stats = sync_orders(conn, client, **kw)
        stats_slot.update(stats.as_dict())
    return stats
