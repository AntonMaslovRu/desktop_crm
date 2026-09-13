"""Сводки: считаются из базы и не уходят дважды за день."""

from datetime import datetime, timedelta

from envo import db, digest
from envo.config import MSK
from envo.telegram import Silent

NOW = datetime(2026, 9, 13, 19, 5, tzinfo=MSK)


def order(conn, status, total, tickets, hours_ago, event=None):
    conn.execute(
        "INSERT INTO orders (afisha_id, event_id, status, ordered_at, tickets_count, total)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (f"o-{status}-{hours_ago}-{total}", event, status, NOW - timedelta(hours=hours_ago),
         tickets, total),
    )


class TestBuild:
    def test_counts_paid_only_within_period(self, conn):
        event = db.fetch_one(conn, "INSERT INTO events (title, display_name, starts_at)"
                                   " VALUES ('UFC 333','UFC 333','2026-10-24') RETURNING id")["id"]
        order(conn, "paid", 108000, 1, 2, event)
        order(conn, "paid", 216000, 2, 5, event)
        order(conn, "cart", 94500, 1, 1, event)
        order(conn, "refund", 94500, 1, 3, event)
        order(conn, "paid", 999999, 9, 40, event)  # вчера, не в периоде
        text = digest.build(conn, NOW - timedelta(hours=14), "Тест", NOW)
        assert "Билетов: <b>3</b> · заказов: <b>2</b> · сумма: <b>324 000 ₽</b>" in text
        assert "Возвратов: <b>1</b>" in text
        assert "UFC 333 — 3" in text

    def test_empty_period(self, conn):
        assert "Продаж за период нет" in digest.build(conn, NOW, "Тест", NOW)


class TestDue:
    def test_evening_sent_once_per_day(self, conn):
        tg = Silent()
        assert digest.due(conn, tg, (9, 19), NOW) == ["digest:9", "digest:19"]
        assert digest.due(conn, tg, (9, 19), NOW) == []

    def test_before_hour_nothing_is_sent(self, conn):
        tg = Silent()
        assert digest.due(conn, tg, (9, 19), NOW.replace(hour=8)) == []
        assert tg.sent == []

    def test_late_digest_is_marked(self, conn):
        tg = Silent()
        digest.due(conn, tg, (9,), NOW.replace(hour=11))
        assert any("с опозданием" in t for t in tg.sent)

    def test_new_day_resets(self, conn):
        tg = Silent()
        digest.due(conn, tg, (9,), NOW)
        assert digest.due(conn, tg, (9,), NOW + timedelta(days=1)) == ["digest:9"]
