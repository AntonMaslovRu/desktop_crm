"""Очередь писем: одно на заказ, ночью ждёт, стоп-кран держит, дубль не уходит."""

from datetime import datetime

from envo import db, letters, mailer, welcome
from envo.config import MSK
from envo.mailer import FakeTransport

DAY = datetime(2026, 9, 13, 12, 0, tzinfo=MSK)
NIGHT = datetime(2026, 9, 13, 23, 0, tzinfo=MSK)
LETTER = letters.welcome(name="Иван", tickets=1, event="UFC 333", event_date=None,
                         order_id="1", instructions="x")


def queue(conn, ref="1", kind="welcome", hold=False):
    return mailer.enqueue(conn, kind=kind, ref_id=ref, contact_id=None, to="a@b.ru",
                          letter=LETTER, hold=hold)


class TestQueue:
    def test_one_letter_per_order(self, conn):
        assert queue(conn) is True
        assert queue(conn) is False
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM letters")["n"] == 1

    def test_same_ref_different_kind_is_allowed(self, conn):
        assert queue(conn, kind="welcome") and queue(conn, kind="cart")


def enable_mail(conn):
    conn.execute("INSERT INTO kv (key, value) VALUES ('mail.hold_all', '0')"
                 " ON CONFLICT (key) DO UPDATE SET value = '0'")


class TestDispatch:
    def test_fresh_database_sends_nothing(self, conn):
        """Стоп-кран включён, пока человек его не снял: свежая база молчит."""
        queue(conn)
        transport = FakeTransport()
        report = mailer.dispatch(conn, transport, now=DAY)
        assert report.held == 1 and transport.sent == []

    def test_sends_by_day(self, conn):
        enable_mail(conn)
        queue(conn)
        transport = FakeTransport()
        report = mailer.dispatch(conn, transport, now=DAY)
        assert report.sent == 1 and transport.sent == [("a@b.ru", "UFC 333. Заказ: 1")]
        assert db.fetch_one(conn, "SELECT state FROM letters")["state"] == "sent"

    def test_waits_at_night(self, conn):
        enable_mail(conn)
        queue(conn)
        transport = FakeTransport()
        report = mailer.dispatch(conn, transport, now=NIGHT)
        assert report.deferred_quiet == 1 and transport.sent == []

    def test_kill_switch_holds_everything(self, conn):
        enable_mail(conn)
        queue(conn)
        conn.execute("UPDATE kv SET value = '1' WHERE key = 'mail.hold_all'")
        report = mailer.dispatch(conn, FakeTransport(), now=DAY)
        assert report.held == 1 and report.sent == 0

    def test_letter_already_in_sent_items_is_skipped(self, conn):
        enable_mail(conn)
        queue(conn, ref="4419077")
        transport = FakeTransport(sent_before={"4419077"})
        report = mailer.dispatch(conn, transport, now=DAY)
        assert report.skipped_duplicate == 1 and transport.sent == []
        assert db.fetch_one(conn, "SELECT state FROM letters")["state"] == "skipped"

    def test_rate_limit_per_minute(self, conn):
        enable_mail(conn)
        for i in range(8):
            queue(conn, ref=str(i))
        report = mailer.dispatch(conn, FakeTransport(), now=DAY)
        assert report.sent == mailer.RATE_PER_MINUTE
        left = db.fetch_one(conn, "SELECT count(*) AS n FROM letters WHERE state = 'queued'")["n"]
        assert left == 8 - mailer.RATE_PER_MINUTE

    def test_transport_failure_keeps_letter_and_counts_attempt(self, conn):
        enable_mail(conn)
        queue(conn)
        report = mailer.dispatch(conn, FakeTransport(fail=True), now=DAY)
        row = db.fetch_one(conn, "SELECT state, attempts, last_error FROM letters")
        assert report.failed == 1 and row["state"] == "queued" and row["attempts"] == 1
        assert "почта недоступна" in row["last_error"]

    def test_gives_up_after_five_failures(self, conn):
        enable_mail(conn)
        queue(conn)
        for _ in range(5):
            mailer.dispatch(conn, FakeTransport(fail=True), now=DAY)
        assert db.fetch_one(conn, "SELECT state FROM letters")["state"] == "failed"

    def test_sent_welcome_marks_the_order(self, conn):
        enable_mail(conn)
        conn.execute("INSERT INTO orders (afisha_id, status) VALUES ('1', 'paid')")
        queue(conn, ref="1")
        mailer.dispatch(conn, FakeTransport(), now=DAY)
        assert db.fetch_one(conn, "SELECT welcome_sent_at FROM orders")["welcome_sent_at"]


def paid_order(conn, *, email="a@b.ru", instructions="Билеты придут.", tickets=1, status="paid",
               ordered_at="now() + interval '1 minute'"):
    event = db.fetch_one(
        conn,
        "INSERT INTO events (title, display_name, starts_at, letter_single, letter_multi)"
        " VALUES ('UFC 333', 'UFC 333', '2026-10-24', %s, %s) RETURNING id",
        (instructions, instructions and instructions + " (несколько)"),
    )["id"]
    contact = db.fetch_one(
        conn, "INSERT INTO contacts (email, name) VALUES (%s, 'Петров Иван') RETURNING id",
        (email or None,),
    )["id"]
    conn.execute(
        f"INSERT INTO orders (afisha_id, event_id, contact_id, status, tickets_count, ordered_at)"
        f" VALUES ('4419077', %s, %s, %s, %s, {ordered_at})", (event, contact, status, tickets),
    )
    db.publish(conn, "order.created", {"order": "4419077"})


class TestWelcomeRule:
    def test_paid_order_gets_a_welcome(self, conn):
        paid_order(conn)
        stats = welcome.process(conn)
        assert stats.queued == 1
        row = db.fetch_one(conn, "SELECT kind, ref_id, to_addr, body_html FROM letters")
        assert (row["kind"], row["ref_id"], row["to_addr"]) == ("welcome", "4419077", "a@b.ru")
        assert "Иван, салют!" in row["body_html"]

    def test_multi_ticket_uses_multi_text(self, conn):
        paid_order(conn, tickets=2)
        welcome.process(conn)
        assert "(несколько)" in db.fetch_one(conn, "SELECT body_html FROM letters")["body_html"]

    def test_processed_once(self, conn):
        paid_order(conn)
        welcome.process(conn)
        assert welcome.process(conn).queued == 0
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM events_log WHERE processed_at IS NULL")["n"] == 0

    def test_cart_is_not_welcomed(self, conn):
        paid_order(conn, status="cart")
        assert welcome.process(conn).queued == 0

    def test_missing_instructions_are_reported_not_skipped_silently(self, conn):
        paid_order(conn, instructions="")
        stats = welcome.process(conn)
        assert stats.queued == 0 and stats.no_instructions == ["UFC 333"]

    def test_orders_before_first_run_are_never_welcomed(self, conn):
        """История до запуска ядра принадлежит прежней автоматике — иначе дубли."""
        paid_order(conn, ordered_at="now() - interval '2 days'")
        stats = welcome.process(conn)
        assert stats.queued == 0 and stats.skipped_old == 1
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM letters")["n"] == 0

    def test_boundary_is_set_once(self, conn):
        welcome.process(conn)
        first = db.fetch_one(conn, "SELECT value FROM kv WHERE key = 'mail.welcome_since'")["value"]
        welcome.process(conn)
        assert db.fetch_one(conn, "SELECT value FROM kv WHERE key = 'mail.welcome_since'")["value"] == first

    def test_no_email_is_counted(self, conn):
        paid_order(conn, email="")
        assert welcome.process(conn).no_email == 1
