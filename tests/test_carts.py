"""Корзины: карантин, фильтры, один человек — одно письмо, «купил сам»."""

from datetime import datetime, timedelta

from envo import carts, db, mailer
from envo.config import MSK
from envo.mailer import FakeTransport
from envo.telegram import Silent

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=MSK)


def event(conn, days_ahead=40, tracking=True):
    return db.fetch_one(
        conn,
        "INSERT INTO events (title, display_name, starts_at, tracking)"
        " VALUES ('UFC 333', 'UFC 333', %s, %s) RETURNING id",
        ((NOW + timedelta(days=days_ahead)).replace(tzinfo=None), tracking),
    )["id"]


def contact(conn, email="a@b.ru", name="Петров Иван"):
    return db.fetch_one(
        conn, "INSERT INTO contacts (email, name) VALUES (%s, %s) RETURNING id", (email, name)
    )["id"]


def cart(conn, ev, ct, *, hours_ago=5, tickets=2, total=189000, afisha_id=None):
    afisha_id = afisha_id or f"c{hours_ago}-{tickets}-{ct}"
    return db.fetch_one(
        conn,
        "INSERT INTO orders (afisha_id, event_id, contact_id, status, ordered_at, tickets_count, total)"
        " VALUES (%s, %s, %s, 'cart', %s, %s, %s) RETURNING id",
        (afisha_id, ev, ct, NOW - timedelta(hours=hours_ago), tickets, total),
    )["id"]


def paid(conn, ev, ct, hours_ago=1):
    conn.execute(
        "INSERT INTO orders (afisha_id, event_id, contact_id, status, ordered_at, tickets_count, total)"
        " VALUES (%s, %s, %s, 'paid', %s, 1, 94500)",
        (f"p{hours_ago}-{ct}", ev, ct, NOW - timedelta(hours=hours_ago)),
    )


def states(conn):
    return {r["afisha_id"]: r["state"] for r in db.fetch_all(
        conn, "SELECT o.afisha_id, f.state FROM cart_followups f JOIN orders o ON o.id = f.order_id")}


class TestLifecycle:
    def test_fresh_cart_waits_in_quarantine(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=1, afisha_id="c1")
        stats = carts.scan(conn, now=NOW)
        assert stats.new == 1 and stats.queued == 0
        assert states(conn) == {"c1": "quarantine"}

    def test_aged_cart_gets_a_letter(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, afisha_id="c1")
        stats = carts.scan(conn, now=NOW)
        assert stats.queued == 1 and states(conn) == {"c1": "ready"}
        row = db.fetch_one(conn, "SELECT kind, ref_id, to_addr, subject FROM letters")
        assert (row["kind"], row["ref_id"], row["to_addr"]) == ("cart", "c1", "a@b.ru")
        assert row["subject"].startswith("UFC 333")

    def test_sent_letter_moves_cart_to_sent(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, afisha_id="c1")
        carts.scan(conn, now=NOW)
        mailer.dispatch(conn, FakeTransport(), now=NOW)
        assert carts.mark_sent(conn) == 1
        assert states(conn) == {"c1": "sent"}
        assert db.fetch_one(conn, "SELECT letter_sent_at FROM cart_followups")["letter_sent_at"]

    def test_scan_is_idempotent(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, afisha_id="c1")
        carts.scan(conn, now=NOW)
        again = carts.scan(conn, now=NOW)
        assert again.new == 0 and again.queued == 0
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM letters")["n"] == 1


class TestFilters:
    def test_bought_later_is_closed_without_letter(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, afisha_id="c1")
        paid(conn, ev, ct, hours_ago=1)
        stats = carts.scan(conn, now=NOW)
        assert stats.bought == 1 and stats.queued == 0
        assert states(conn) == {"c1": "bought"}

    def test_bought_before_the_cart_does_not_count(self, conn):
        ev, ct = event(conn), contact(conn)
        paid(conn, ev, ct, hours_ago=10)
        cart(conn, ev, ct, hours_ago=5, afisha_id="c1")
        assert carts.scan(conn, now=NOW).queued == 1

    def test_wholesale_is_not_chased(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, tickets=5)
        assert carts.scan(conn, now=NOW).new == 0

    def test_too_close_to_event(self, conn):
        ev, ct = event(conn, days_ahead=3), contact(conn)
        cart(conn, ev, ct, hours_ago=5)
        assert carts.scan(conn, now=NOW).new == 0

    def test_untracked_event(self, conn):
        ev, ct = event(conn, tracking=False), contact(conn)
        cart(conn, ev, ct, hours_ago=5)
        assert carts.scan(conn, now=NOW).new == 0

    def test_quarantined_cart_is_excluded_when_event_goes_dead(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=1, afisha_id="c1")
        carts.scan(conn, now=NOW)
        conn.execute("UPDATE events SET tracking = false")
        stats = carts.scan(conn, now=NOW)
        assert stats.excluded == 1 and states(conn) == {"c1": "excluded"}


class TestOnePersonOneLetter:
    def test_two_carts_same_person_get_one_letter_for_the_bigger(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, total=100000, afisha_id="small")
        cart(conn, ev, ct, hours_ago=6, total=300000, afisha_id="big")
        stats = carts.scan(conn, now=NOW)
        assert stats.queued == 1
        assert db.fetch_one(conn, "SELECT ref_id FROM letters")["ref_id"] == "big"

    def test_person_written_before_is_not_written_again(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5, afisha_id="c1")
        carts.scan(conn, now=NOW)
        cart(conn, ev, ct, hours_ago=8, afisha_id="c2")
        assert carts.scan(conn, now=NOW).queued == 0

    def test_cap_per_run(self, conn):
        ev = event(conn)
        for i in range(12):
            ct = contact(conn, email=f"u{i}@b.ru", name=f"Иван {i}")
            cart(conn, ev, ct, hours_ago=5, afisha_id=f"c{i}")
        stats = carts.scan(conn, now=NOW)
        assert stats.queued == carts.MAX_PER_RUN and stats.capped


class TestReport:
    def test_silent_when_nothing_happened(self, conn):
        tg = Silent()
        carts.run(conn, tg, now=NOW)
        assert tg.sent == []

    def test_reports_queue_with_names(self, conn):
        ev, ct = event(conn), contact(conn)
        cart(conn, ev, ct, hours_ago=5)
        tg = Silent()
        carts.run(conn, tg, now=NOW)
        assert "Иван · UFC 333 · 189 000 ₽" in tg.sent[0]
