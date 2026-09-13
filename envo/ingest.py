"""Заказы, билеты и события из Афиши в базу.

Правило одно: повторный прогон ничего не дублирует и ничего не теряет. Окно берём с
запасом, потому что пропущенный прогон должен догоняться следующим, а не оператором.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Json

from envo import db
from envo.afisha import AfishaClient, Event, Order
from envo.names import first_name
from envo.normalize import normalize_email, normalize_phone

WINDOW_DAYS = 3  # окно сканирования продаж; корзины смотрят глубже
SYSTEM = "система"

# Статусы заказа у Афиши, которые мы видели живьём. Всё остальное — unknown, в отчёт.
AFISHA_STATUS = {0: "cart", 1: "paid"}


@dataclass
class IngestStats:
    events_new: int = 0
    events_updated: int = 0
    orders_new: int = 0
    orders_updated: int = 0
    orders_unchanged: int = 0
    tickets_new: int = 0
    returns: int = 0
    contacts_new: int = 0
    contacts_merged: int = 0
    unknown_events: list[str] = field(default_factory=list)
    unknown_statuses: dict[str, int] = field(default_factory=dict)
    skipped: bool = False

    def as_dict(self) -> dict:
        return {
            "events_new": self.events_new,
            "events_updated": self.events_updated,
            "orders_new": self.orders_new,
            "orders_updated": self.orders_updated,
            "orders_unchanged": self.orders_unchanged,
            "tickets_new": self.tickets_new,
            "returns": self.returns,
            "contacts_new": self.contacts_new,
            "contacts_merged": self.contacts_merged,
            "unknown_events": self.unknown_events,
            "unknown_statuses": self.unknown_statuses,
            "skipped": self.skipped,
        }


def order_status(order: Order, stats: IngestStats | None = None) -> str:
    if order.is_returned:
        return "refund"
    status = AFISHA_STATUS.get(order.status)
    if status is None:
        if stats is not None:
            stats.unknown_statuses[str(order.status)] = (
                stats.unknown_statuses.get(str(order.status), 0) + 1
            )
        return "unknown"
    return status


# ---------- события ----------


def sync_events(conn: psycopg.Connection, events: list[Event], stats: IngestStats) -> None:
    """Каталог событий из Афиши. Ручные правки живут в event_facts и не затираются."""
    for event in events:
        row = db.fetch_one(
            conn,
            """
            INSERT INTO events (afisha_id, title, display_name, starts_at,
                                afisha_status, afisha_venue_id, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (afisha_id) DO UPDATE
               SET title = EXCLUDED.title,
                   starts_at = EXCLUDED.starts_at,
                   afisha_status = EXCLUDED.afisha_status,
                   afisha_venue_id = EXCLUDED.afisha_venue_id,
                   raw = EXCLUDED.raw,
                   updated_at = now()
             WHERE (events.title, events.starts_at, events.afisha_status)
                   IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.starts_at, EXCLUDED.afisha_status)
            RETURNING (xmax = 0) AS is_new
            """,
            (event.id, event.name, event.display_name, event.starts_at.replace(tzinfo=None),
             event.status, event.venue_id, Json(event.raw or {})),
        )
        if row is None:
            continue  # ничего не изменилось
        if row["is_new"]:
            stats.events_new += 1
        else:
            stats.events_updated += 1


# ---------- клиенты ----------


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


def upsert_contact(conn: psycopg.Connection, order: Order, stats: IngestStats) -> int | None:
    """Находит или заводит клиента.

    Ключи по убыванию надёжности: id покупателя у Афиши, почта, телефон. Совпадение
    любого — тот же человек; несколько карточек схлопываются в самую старую.
    """
    email = normalize_email(order.email)
    phone = normalize_phone(order.phone)
    customer_id = order.customer_id or ""
    if not email and not phone and not customer_id:
        return None

    found = db.fetch_all(
        conn,
        """
        SELECT id FROM contacts
         WHERE merged_into IS NULL
           AND ((%s <> '' AND afisha_customer_id = %s)
             OR (%s <> '' AND email = %s)
             OR (%s <> '' AND phone = %s))
         ORDER BY id
        """,
        (customer_id, customer_id, email, email, phone, phone),
    )

    if not found:
        row = db.fetch_one(
            conn,
            "INSERT INTO contacts (afisha_customer_id, email, phone, name, salutation, consent_afisha)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (customer_id or None, email or None, phone or None, order.customer_name or None,
             first_name(order.customer_name) or None, order.subscribed),
        )
        stats.contacts_new += 1
        remember_contacts(conn, row["id"], email, phone)
        return row["id"]

    keeper = found[0]["id"]
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
        db.audit(conn, SYSTEM, "склейка клиентов", {"kept": keeper, "hidden": extra["id"]})
        stats.contacts_merged += 1

    # Основным становится самый свежий адрес: человек пишет с того, с которого заказал.
    conn.execute(
        """
        UPDATE contacts
           SET afisha_customer_id = COALESCE(afisha_customer_id, NULLIF(%s, '')),
               email = COALESCE(NULLIF(%s, ''), email),
               phone = COALESCE(NULLIF(%s, ''), phone),
               name  = COALESCE(name, %s),
               salutation = COALESCE(salutation, %s),
               consent_afisha = COALESCE(%s, consent_afisha)
         WHERE id = %s
        """,
        (customer_id, email, phone, order.customer_name or None,
         first_name(order.customer_name) or None, order.subscribed, keeper),
    )
    remember_contacts(conn, keeper, email, phone)
    return keeper


# ---------- заказы и билеты ----------


def event_pk(conn: psycopg.Connection, afisha_event_id: int | None) -> int | None:
    if not afisha_event_id:
        return None
    row = db.fetch_one(conn, "SELECT id FROM events WHERE afisha_id = %s", (afisha_event_id,))
    return row["id"] if row else None


def category_for(conn: psycopg.Connection, event_id: int | None, sector: str) -> int | None:
    """Сектор → категория по алиасам. Незнакомый сектор не угадывается."""
    if not event_id or not sector:
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
    agents: dict[int, str] | None = None,
    stats: IngestStats | None = None,
) -> IngestStats:
    """Один прогон синхронизации заказов. Идемпотентен: гонять можно сколько угодно."""
    stats = stats or IngestStats()
    agents = agents or {}
    today = today or date.today()
    end = today + timedelta(days=1)
    start = end - timedelta(days=window_days + 1)

    for order in client.orders(start, end):
        status = order_status(order, stats)
        contact_id = upsert_contact(conn, order, stats)
        event_id = event_pk(conn, order.event_id)
        if order.event_id and event_id is None:
            stats.unknown_events.append(str(order.event_id))

        existing = db.fetch_one(
            conn, "SELECT id, status, fingerprint FROM orders WHERE afisha_id = %s", (order.id,)
        )
        row = db.fetch_one(
            conn,
            """
            INSERT INTO orders (afisha_id, event_id, contact_id, channel, status, afisha_status,
                                agent_id, showcase, ordered_at, total, tickets_count,
                                fingerprint, raw)
            VALUES (%s, %s, %s, 'afisha', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (afisha_id) DO UPDATE
               SET status = EXCLUDED.status,
                   afisha_status = EXCLUDED.afisha_status,
                   event_id = COALESCE(orders.event_id, EXCLUDED.event_id),
                   contact_id = COALESCE(orders.contact_id, EXCLUDED.contact_id),
                   agent_id = COALESCE(EXCLUDED.agent_id, orders.agent_id),
                   showcase = COALESCE(EXCLUDED.showcase, orders.showcase),
                   total = EXCLUDED.total,
                   tickets_count = EXCLUDED.tickets_count,
                   fingerprint = EXCLUDED.fingerprint,
                   raw = EXCLUDED.raw,
                   last_seen_at = now()
            RETURNING id
            """,
            (order.id, event_id, contact_id, status, order.status, order.agent_id,
             agents.get(order.agent_id) if order.agent_id else None, order.ordered_at,
             order.total, order.tickets_count, order.fingerprint, Json(order.raw)),
        )
        order_pk = row["id"]

        if existing is None:
            stats.orders_new += 1
            db.publish(conn, "order.created", {"order": order.id, "status": status,
                                               "event": event_id, "total": float(order.total)})
        elif existing["fingerprint"] == order.fingerprint:
            stats.orders_unchanged += 1
            continue  # ничего не изменилось — order.info не дёргаем
        else:
            stats.orders_updated += 1
            if existing["status"] != "refund" and status == "refund":
                stats.returns += 1
                db.publish(conn, "order.returned", {"order": order.id, "event": event_id})
            elif existing["status"] == "cart" and status == "paid":
                db.publish(conn, "order.paid", {"order": order.id, "event": event_id,
                                                "total": float(order.total)})

        ticket_status = "refund" if order.is_returned else ("cart" if status == "cart" else "sold")
        for ticket in client.tickets(order.id):
            inserted = db.fetch_one(
                conn,
                """
                INSERT INTO tickets (order_id, afisha_id, category_id, sector, barcode, price,
                                     refundable, status, afisha_status, sold_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id, afisha_id) DO UPDATE
                   SET price = EXCLUDED.price,
                       status = EXCLUDED.status,
                       afisha_status = EXCLUDED.afisha_status,
                       category_id = COALESCE(tickets.category_id, EXCLUDED.category_id)
                RETURNING (xmax = 0) AS is_new
                """,
                (order_pk, ticket.id, category_for(conn, event_id, ticket.sector),
                 ticket.sector, ticket.barcode, ticket.price, ticket.refundable,
                 ticket_status, ticket.status, order.ordered_at),
            )
            if inserted and inserted["is_new"]:
                stats.tickets_new += 1

    return stats


def run(conn: psycopg.Connection, client: AfishaClient, **kw) -> IngestStats:
    """Полный прогон: события, витрины, заказы — с журналом и блокировкой «одна копия».

    Если прогон уже идёт в другом процессе, этот тихо пропускается: это не ошибка.
    """
    stats = IngestStats()
    if not db.single_run(conn, "sales.sync"):
        stats.skipped = True
        return stats
    with db.run_record(conn, "sales.sync") as slot:
        sync_events(conn, client.events(), stats)
        sync_orders(conn, client, agents=client.agents(), stats=stats, **kw)
        slot.update(stats.as_dict())
    return stats
