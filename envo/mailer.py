"""Очередь исходящих писем.

Гарантии, ради которых очередь существует:
- одно письмо на заказ или корзину — уникальный индекс в базе, а не аккуратность кода;
- клиентам пишем только в окно 09:00–21:00 МСК, ночью письма ждут;
- не больше N писем в минуту, чтобы не выглядеть рассылкой;
- стоп-кран: тумблер «держать всё» оставляет письма в очереди со статусом held;
- перед отправкой страховка по «Отправленным»: если письмо с этим номером заказа
  уже уходило (например, из старой рутины), не дублируем, а помечаем отправленным.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import psycopg

from envo import db
from envo.config import MSK
from envo.letters import Letter

RATE_PER_MINUTE = 5
MAX_PER_RUN = 30  # предохранитель: больше за раз — что-то не так, останавливаемся


class Transport(Protocol):
    """Почтовый транспорт. Graph — боевой, Fake — для тестов."""

    def send(self, to: str, subject: str, html: str) -> str:
        """Возвращает id письма у провайдера."""

    def already_sent(self, marker: str) -> bool:
        """Есть ли в «Отправленных» письмо с этим маркером (номером заказа)."""


@dataclass
class SendReport:
    sent: int = 0
    skipped_duplicate: int = 0
    held: int = 0
    failed: int = 0
    deferred_quiet: int = 0


def enqueue(
    conn: psycopg.Connection,
    *,
    kind: str,
    ref_id: str,
    contact_id: int | None,
    to: str,
    letter: Letter,
    hold: bool = False,
) -> bool:
    """Ставит письмо в очередь. Повтор по тому же (kind, ref_id) — тихий no-op."""
    row = db.fetch_one(
        conn,
        """
        INSERT INTO letters (kind, ref_id, contact_id, to_addr, subject, body_html, state)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (kind, ref_id) DO NOTHING
        RETURNING id
        """,
        (kind, ref_id, contact_id, to, letter.subject, letter.html,
         "held" if hold else "queued"),
    )
    return row is not None


def is_quiet(now: datetime, quiet_from: int = 21, quiet_to: int = 9) -> bool:
    """Ночь по Москве: письма клиентам не уходят."""
    hour = now.astimezone(MSK).hour
    return hour >= quiet_from or hour < quiet_to


def kill_switch_on(conn: psycopg.Connection) -> bool:
    """Отправка выключена, пока человек явно не включил её. Свежая база — молчит."""
    row = db.fetch_one(conn, "SELECT value FROM kv WHERE key = 'mail.hold_all'")
    return row is None or row["value"] != "0"


def dispatch(
    conn: psycopg.Connection,
    transport: Transport,
    *,
    now: datetime | None = None,
    quiet_from: int = 21,
    quiet_to: int = 9,
) -> SendReport:
    """Дренаж очереди. Каждое письмо фиксируется в своём состоянии, ошибки не скрываются."""
    now = now or datetime.now(MSK)
    report = SendReport()
    if kill_switch_on(conn):
        report.held = db.fetch_one(conn, "SELECT count(*) AS n FROM letters WHERE state = 'queued'")["n"]
        return report
    if is_quiet(now, quiet_from, quiet_to):
        report.deferred_quiet = db.fetch_one(conn, "SELECT count(*) AS n FROM letters WHERE state = 'queued'")["n"]
        return report

    minute_ago = now - timedelta(minutes=1)
    sent_recently = db.fetch_one(
        conn, "SELECT count(*) AS n FROM letters WHERE state = 'sent' AND sent_at >= %s",
        (minute_ago,),
    )["n"]
    budget = max(0, min(RATE_PER_MINUTE - sent_recently, MAX_PER_RUN))

    queue = db.fetch_all(
        conn,
        "SELECT id, kind, ref_id, to_addr, subject, body_html FROM letters"
        " WHERE state = 'queued' ORDER BY created_at LIMIT %s",
        (budget,),
    )
    for letter in queue:
        marker = letter["ref_id"]
        try:
            if transport.already_sent(marker):
                conn.execute(
                    "UPDATE letters SET state = 'skipped', last_error = 'уже отправлено ранее',"
                    " sent_at = now() WHERE id = %s",
                    (letter["id"],),
                )
                report.skipped_duplicate += 1
                _mark_order(conn, letter)
                continue
            message_id = transport.send(letter["to_addr"], letter["subject"], letter["body_html"])
        except Exception as exc:  # noqa: BLE001 — ошибка транспорта не должна ронять прогон
            conn.execute(
                "UPDATE letters SET attempts = attempts + 1, last_error = %s,"
                " state = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'queued' END"
                " WHERE id = %s",
                (str(exc)[:500], letter["id"]),
            )
            report.failed += 1
            continue
        conn.execute(
            "UPDATE letters SET state = 'sent', provider_message_id = %s, sent_at = %s,"
            " attempts = attempts + 1 WHERE id = %s",
            (message_id, now, letter["id"]),
        )
        _mark_order(conn, letter)
        db.publish(conn, "letter.sent", {"kind": letter["kind"], "ref": marker, "to": letter["to_addr"]})
        report.sent += 1
    return report


def _mark_order(conn: psycopg.Connection, letter: dict) -> None:
    """Отметка на заказе: вэлком ушёл. Столбец «Вэлком» на экране заказов живёт отсюда."""
    if letter["kind"] == "welcome":
        conn.execute(
            "UPDATE orders SET welcome_sent_at = coalesce(welcome_sent_at, now())"
            " WHERE afisha_id = %s",
            (letter["ref_id"],),
        )


class FakeTransport:
    """Транспорт для тестов: помнит, что отправил, и умеет притворяться, что письмо уже было."""

    def __init__(self, sent_before: set[str] | None = None, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.sent_before = sent_before or set()
        self.fail = fail

    def send(self, to: str, subject: str, html: str) -> str:
        if self.fail:
            raise ConnectionError("почта недоступна")
        self.sent.append((to, subject))
        return f"msg-{len(self.sent)}"

    def already_sent(self, marker: str) -> bool:
        return marker in self.sent_before
