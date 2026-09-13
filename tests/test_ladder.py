"""Ступени: считаем по билетам, закрываем один раз, ловим продажи мимо."""

from datetime import datetime, timezone

from envo import db, ladder
from envo.telegram import Silent

T0 = datetime(2026, 9, 2, 10, tzinfo=timezone.utc)


def setup_ladder(conn, prices=((94500, 7), (108000, 17), (126000, 9))):
    event = db.fetch_one(
        conn,
        "INSERT INTO events (afisha_id, title, display_name, starts_at)"
        " VALUES (1, 'UFC 333', 'UFC 333', '2026-10-24 18:00') RETURNING id",
    )["id"]
    cat = db.fetch_one(
        conn, "INSERT INTO categories (event_id, name) VALUES (%s, 'Кат. 6') RETURNING id", (event,)
    )["id"]
    for idx, (price, quota) in enumerate(prices, 1):
        conn.execute("INSERT INTO steps (category_id, idx, price, quota) VALUES (%s, %s, %s, %s)",
                     (cat, idx, price, quota))
    return event, cat


def sell(conn, event, cat, price, *, n=1, refundable=False, status="paid", at=T0):
    order = db.fetch_one(
        conn,
        "INSERT INTO orders (afisha_id, event_id, status, ordered_at, tickets_count, total)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (f"o{price}-{at.timestamp()}-{n}-{refundable}-{status}", event, status, at, n, price * n),
    )["id"]
    for i in range(n):
        conn.execute(
            "INSERT INTO tickets (order_id, afisha_id, category_id, sector, price, refundable,"
            " status, sold_at) VALUES (%s, %s, %s, 'Кат. 6', %s, %s, %s, %s)",
            (order, f"t{order}-{i}", cat, price, refundable,
             "refund" if status == "refund" else "sold", at),
        )


class TestCounting:
    def test_tickets_land_on_steps_by_price(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 94500, n=3)
        sell(conn, event, cat, 108000, n=2)
        state = ladder.refresh(conn, cat)
        assert [s.sold for s in state.steps] == [3, 2, 0]
        assert state.active.idx == 1

    def test_refundable_goes_to_open_step(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 94500, n=2)
        sell(conn, event, cat, 108990, refundable=True)  # возвратный по 94 500 + надбавка
        state = ladder.refresh(conn, cat)
        assert state.steps[0].sold == 3
        assert state.off_ladder == {}

    def test_unknown_price_is_off_ladder(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 99000, n=2)
        state = ladder.refresh(conn, cat)
        assert state.off_ladder == {99000.0: 2}
        assert sum(s.sold for s in state.steps) == 0

    def test_refunds_and_carts_do_not_count(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 94500, status="refund")
        sell(conn, event, cat, 94500, status="cart")
        state = ladder.refresh(conn, cat)
        assert state.steps[0].sold == 0


class TestClosing:
    def test_step_closes_once_and_names_next_price(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 94500, n=7)
        tg = Silent()
        state = ladder.refresh(conn, cat)
        first = ladder.close_steps(conn, state, tg)
        second = ladder.close_steps(conn, ladder.refresh(conn, cat), tg)
        assert len(first) == 1 and second == []
        assert "108 000 ₽" in first[0] and "120 000 ₽" in first[0]
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM events_log WHERE topic='step.closed'")["n"] == 1

    def test_last_step_reports_sold_out(self, conn):
        event, cat = setup_ladder(conn, prices=((94500, 1),))
        sell(conn, event, cat, 94500)
        text = ladder.close_steps(conn, ladder.refresh(conn, cat), Silent())[0]
        assert "распродана" in text


class TestOffLadderAlerts:
    def test_reported_only_when_grows(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 99000)
        tg = Silent()
        assert ladder.report_off_ladder(conn, ladder.refresh(conn, cat), tg)
        assert ladder.report_off_ladder(conn, ladder.refresh(conn, cat), tg) is None
        sell(conn, event, cat, 99000, at=datetime(2026, 9, 3, tzinfo=timezone.utc))
        assert ladder.report_off_ladder(conn, ladder.refresh(conn, cat), tg)
        assert len(tg.sent) == 2


class TestRun:
    def test_summary_only_when_counts_change(self, conn):
        event, cat = setup_ladder(conn)
        sell(conn, event, cat, 94500, n=2)
        tg = Silent()
        ladder.run(conn, tg)
        ladder.run(conn, tg)
        assert len(tg.sent) == 1, "без изменений Telegram не беспокоим"
        assert "2 из 7" in tg.sent[0]

    def test_past_events_are_ignored(self, conn):
        event, cat = setup_ladder(conn)
        conn.execute("UPDATE events SET starts_at = '2026-01-01'")
        sell(conn, event, cat, 94500)
        stats = ladder.run(conn, Silent())
        assert stats["ladders"] == 0
