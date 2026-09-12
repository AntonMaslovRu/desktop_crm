"""Ингест: повторный прогон не должен ни дублировать, ни терять."""

import json
from datetime import date

import pytest

from envo import db
from envo.afisha import AfishaClient
from envo.ingest import sync_orders

TODAY = date(2026, 9, 12)


def crm(orders, tickets):
    """Подставная CRM: отдаёт заказы и билеты без сети."""

    def opener(url: str) -> bytes:
        if "crm.order.list" in url:
            return json.dumps({"status": "0", "result": orders}).encode()
        if "crm.order.info" in url:
            order_id = url.split("order_id=")[1].split("&")[0]
            return json.dumps({"status": "0", "result": [{"tickets": tickets.get(order_id, [])}]}).encode()
        return json.dumps({"status": "0", "result": []}).encode()

    return AfishaClient("u", "p", "1", opener=opener)


def order_row(oid="4419077", **kw):
    row = {
        "id": oid,
        "status": 1,
        "sum": "108000",
        "tickets_count": 1,
        "order_date": "2026-09-12 14:22:31",
        "customer": {"name": "Петров Александр", "email": "a.petrov@mail.ru",
                     "phone": "+79161234567"},
    }
    row.update(kw)
    return row


def ticket_row(tid="t1", event_id=70823021, sector="Категория 6", price="108000"):
    return {"id": tid, "event_id": event_id, "sector": sector, "price": price, "barcode": "b" + tid}


def make_event(conn, afisha_id=70823021, name="UFC 333"):
    row = db.fetch_one(
        conn,
        "INSERT INTO events (afisha_id, title, display_name, starts_at)"
        " VALUES (%s, %s, %s, '2026-10-24 22:00+04') RETURNING id",
        (afisha_id, name, name),
    )
    return row["id"]


class TestIdempotency:
    def test_second_run_adds_nothing(self, conn):
        make_event(conn)
        client = crm([order_row()], {"4419077": [ticket_row()]})

        first = sync_orders(conn, client, today=TODAY)
        second = sync_orders(conn, client, today=TODAY)

        assert (first.orders_new, first.tickets_new) == (1, 1)
        assert (second.orders_new, second.tickets_new) == (0, 0)
        assert second.orders_updated == 1
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM tickets")["n"] == 1
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM orders")["n"] == 1

    def test_new_ticket_in_known_order_is_picked_up(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row()], {"4419077": [ticket_row()]}), today=TODAY)
        stats = sync_orders(
            conn,
            crm([order_row(tickets_count=2)], {"4419077": [ticket_row(), ticket_row("t2")]}),
            today=TODAY,
        )
        assert stats.tickets_new == 1
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM tickets")["n"] == 2


class TestReturns:
    def test_return_is_registered_once(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row()], {"4419077": [ticket_row()]}), today=TODAY)

        returned = crm([order_row(is_returned=True)], {"4419077": [ticket_row()]})
        first = sync_orders(conn, returned, today=TODAY)
        second = sync_orders(conn, returned, today=TODAY)

        assert first.returns == 1
        assert second.returns == 0, "повторный прогон не должен слать возврат заново"
        assert db.fetch_one(conn, "SELECT status FROM orders")["status"] == "возврат"
        assert db.fetch_one(conn, "SELECT status FROM tickets")["status"] == "возврат"

    def test_return_publishes_event(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row()], {"4419077": [ticket_row()]}), today=TODAY)
        sync_orders(conn, crm([order_row(is_returned=True)], {"4419077": [ticket_row()]}),
                    today=TODAY)
        topics = [r["topic"] for r in db.fetch_all(conn, "SELECT topic FROM events_log ORDER BY id")]
        assert topics == ["order.created", "order.returned"]


class TestContacts:
    def test_same_person_is_not_duplicated(self, conn):
        make_event(conn)
        orders = [order_row("1"), order_row("2", order_date="2026-09-11 10:00:00")]
        sync_orders(conn, crm(orders, {"1": [ticket_row()], "2": [ticket_row("t2")]}), today=TODAY)
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM contacts")["n"] == 1

    def test_second_email_same_phone_is_merged(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row("1")], {"1": [ticket_row()]}), today=TODAY)

        moved = order_row("2")
        moved["customer"] = {"name": "Петров Александр", "email": "new@mail.ru",
                             "phone": "+79161234567"}
        stats = sync_orders(conn, crm([moved], {"2": [ticket_row("t2")]}), today=TODAY)

        assert stats.contacts_new == 0, "тот же телефон — тот же человек, новую карточку не заводим"
        live = db.fetch_all(conn, "SELECT id, email FROM contacts WHERE merged_into IS NULL")
        assert len(live) == 1
        assert live[0]["email"] == "new@mail.ru", "основным становится свежий адрес"
        known = {r["email"] for r in db.fetch_all(conn, "SELECT email FROM contact_emails")}
        assert known == {"a.petrov@mail.ru", "new@mail.ru"}, "старый адрес нельзя терять"

    def test_two_cards_are_merged_when_a_later_order_ties_them(self, conn):
        make_event(conn)
        by_mail = order_row("1")
        by_mail["customer"] = {"name": "Пётр Кузнецов", "email": "p@mail.ru", "phone": ""}
        by_phone = order_row("2")
        by_phone["customer"] = {"name": "Пётр Кузнецов", "email": "", "phone": "+79990001122"}
        sync_orders(conn, crm([by_mail, by_phone], {"1": [ticket_row()], "2": [ticket_row("t2")]}),
                    today=TODAY)
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM contacts")["n"] == 2

        both = order_row("3")
        both["customer"] = {"name": "Пётр Кузнецов", "email": "p@mail.ru", "phone": "+79990001122"}
        stats = sync_orders(conn, crm([both], {"3": [ticket_row("t3")]}), today=TODAY)

        assert stats.contacts_merged == 1
        live = db.fetch_all(conn, "SELECT id FROM contacts WHERE merged_into IS NULL")
        assert len(live) == 1, "заказ, связавший почту и телефон, склеивает карточки"
        orders_on_live = db.fetch_one(
            conn, "SELECT count(*) AS n FROM orders WHERE contact_id = %s", (live[0]["id"],)
        )["n"]
        assert orders_on_live == 3, "все заказы переезжают на живую карточку"

    def test_contact_without_contacts_is_skipped(self, conn):
        make_event(conn)
        anon = order_row("1")
        anon["customer"] = {"name": "Без контактов", "email": "", "phone": ""}
        sync_orders(conn, crm([anon], {"1": [ticket_row()]}), today=TODAY)
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM contacts")["n"] == 0
        assert db.fetch_one(conn, "SELECT contact_id FROM orders")["contact_id"] is None

    def test_salutation_is_filled_from_full_name(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row()], {"4419077": [ticket_row()]}), today=TODAY)
        assert db.fetch_one(conn, "SELECT salutation FROM contacts")["salutation"] == "Александр"


class TestCatalog:
    def test_unknown_event_is_reported_not_invented(self, conn):
        stats = sync_orders(conn, crm([order_row()], {"4419077": [ticket_row()]}), today=TODAY)
        assert stats.unknown_events == ["70823021"]
        assert db.fetch_one(conn, "SELECT event_id FROM orders")["event_id"] is None
        assert db.fetch_one(conn, "SELECT count(*) AS n FROM tickets")["n"] == 1, (
            "билет всё равно записан: терять продажу из-за незаведённого события нельзя"
        )

    def test_sector_matches_category_by_alias(self, conn):
        event_id = make_event(conn)
        cat = db.fetch_one(
            conn, "INSERT INTO categories (event_id, name) VALUES (%s, 'Кат. 6') RETURNING id",
            (event_id,),
        )["id"]
        conn.execute("INSERT INTO category_aliases (category_id, alias) VALUES (%s, 'верхний ярус')",
                     (cat,))
        sync_orders(
            conn,
            crm([order_row()], {"4419077": [ticket_row(sector="Верхний ярус секторов 101-115")]}),
            today=TODAY,
        )
        assert db.fetch_one(conn, "SELECT category_id FROM tickets")["category_id"] == cat

    def test_unknown_sector_stays_unmatched(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row()], {"4419077": [ticket_row(sector="Партер")]}),
                    today=TODAY)
        assert db.fetch_one(conn, "SELECT category_id FROM tickets")["category_id"] is None


class TestCarts:
    def test_unfinished_order_is_stored_as_cart(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row(status=0)], {"4419077": [ticket_row()]}), today=TODAY)
        assert db.fetch_one(conn, "SELECT status FROM orders")["status"] == "корзина"

    def test_cart_becomes_paid_without_duplicating(self, conn):
        make_event(conn)
        sync_orders(conn, crm([order_row(status=0)], {"4419077": [ticket_row()]}), today=TODAY)
        sync_orders(conn, crm([order_row(status=1)], {"4419077": [ticket_row()]}), today=TODAY)
        rows = db.fetch_all(conn, "SELECT status FROM orders")
        assert len(rows) == 1 and rows[0]["status"] == "оплачен"


class TestRunRecord:
    def test_failed_run_is_recorded_and_reraised(self, conn):
        with pytest.raises(RuntimeError):
            with db.run_record(conn, "sales.sync"):
                raise RuntimeError("CRM недоступна")
        row = db.fetch_one(conn, "SELECT status, error FROM runs")
        assert row["status"] == "failed" and "CRM недоступна" in row["error"]

    def test_successful_run_saves_stats(self, conn):
        with db.run_record(conn, "sales.sync") as stats:
            stats["билетов новых"] = 3
        row = db.fetch_one(conn, "SELECT status, stats FROM runs")
        assert row["status"] == "ok" and row["stats"]["билетов новых"] == 3
