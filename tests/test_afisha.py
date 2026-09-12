"""Клиент CRM проверяется на подставном ответе: сеть в тестах не нужна."""

import json
import unittest
from datetime import date

from envo.afisha import AfishaClient, AfishaError, flatten


def canned(payload: dict, calls: list | None = None):
    def opener(url: str) -> bytes:
        if calls is not None:
            calls.append(url)
        return json.dumps(payload).encode()

    return opener


class Flatten(unittest.TestCase):
    def test_handles_every_shape_crm_returns(self):
        self.assertEqual(flatten(None), [])
        self.assertEqual(flatten({"a": 1}), [{"a": 1}])
        self.assertEqual(flatten([{"a": 1}]), [{"a": 1}])
        self.assertEqual(flatten([[{"a": 1}, {"b": 2}]]), [{"a": 1}, {"b": 2}])

    def test_skips_junk(self):
        self.assertEqual(flatten([None, "мусор", {"a": 1}]), [{"a": 1}])


class Signing(unittest.TestCase):
    def test_city_id_is_sent_except_for_city_list(self):
        calls: list[str] = []
        client = AfishaClient("u", "p", "777", opener=canned({"status": "0", "result": []}, calls))
        client.call("crm.order.list")
        client.call("crm.city.list")
        self.assertIn("city_id=777", calls[0])
        self.assertNotIn("city_id", calls[1])

    def test_auth_has_three_parts(self):
        calls: list[str] = []
        client = AfishaClient("u", "p", "1", opener=canned({"status": "0", "result": []}, calls))
        client.call("crm.event.list")
        auth = [p for p in calls[0].split("&") if p.startswith("auth=")][0]
        self.assertEqual(auth.replace("auth=", "").count("%3A"), 2)


class Errors(unittest.TestCase):
    def test_api_error_is_not_retried(self):
        calls: list[str] = []
        client = AfishaClient("u", "p", "1", opener=canned({"status": "1", "error": "нет прав"}, calls))
        with self.assertRaises(AfishaError):
            client.call("crm.order.list")
        self.assertEqual(len(calls), 1)

    def test_network_failure_is_retried_then_raises(self):
        attempts = []

        def flaky(url: str) -> bytes:
            attempts.append(url)
            raise OSError("сеть отвалилась")

        client = AfishaClient("u", "p", "1", opener=flaky, sleep=lambda _: None)
        with self.assertRaises(AfishaError):
            client.call("crm.order.list")
        self.assertEqual(len(attempts), 3)

    def test_retry_succeeds_on_second_attempt(self):
        state = {"n": 0}

        def flaky(url: str) -> bytes:
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("сеть отвалилась")
            return json.dumps({"status": "0", "result": [{"id": 1}]}).encode()

        client = AfishaClient("u", "p", "1", opener=flaky, sleep=lambda _: None)
        self.assertEqual(client.call("crm.order.list"), [{"id": 1}])


class Parsing(unittest.TestCase):
    ORDER = {
        "id": 4419077,
        "status": 1,
        "sum": "108000",
        "tickets_count": 1,
        "order_date": "2026-09-12 14:22:31",
        "customer": {"name": "Петров Александр", "email": " A.Petrov@Mail.ru ", "phone": "+79161234567"},
        "agent_id": "12",
    }

    def test_order_fields_are_cleaned(self):
        client = AfishaClient("u", "p", "1", opener=canned({"status": "0", "result": [self.ORDER]}))
        order = client.orders(date(2026, 9, 1), date(2026, 9, 13))[0]
        self.assertEqual(order.id, "4419077")
        self.assertEqual(order.email, "a.petrov@mail.ru")
        self.assertEqual(order.phone, "79161234567")
        self.assertTrue(order.is_paid)
        self.assertFalse(order.is_cart)

    def test_cart_is_an_unfinished_order(self):
        row = dict(self.ORDER, status=0)
        client = AfishaClient("u", "p", "1", opener=canned({"status": "0", "result": [row]}))
        order = client.orders(date(2026, 9, 1), date(2026, 9, 13))[0]
        self.assertTrue(order.is_cart)
        self.assertFalse(order.is_paid)

    def test_returned_order_is_neither_paid_nor_cart(self):
        row = dict(self.ORDER, is_returned=True)
        client = AfishaClient("u", "p", "1", opener=canned({"status": "0", "result": [row]}))
        order = client.orders(date(2026, 9, 1), date(2026, 9, 13))[0]
        self.assertFalse(order.is_paid)
        self.assertFalse(order.is_cart)

    def test_broken_price_does_not_crash_ticket(self):
        payload = {"status": "0", "result": [{"tickets": [
            {"id": "t1", "event_id": "70823021", "sector": "Категория 6", "price": "не число"},
        ]}]}
        client = AfishaClient("u", "p", "1", opener=canned(payload))
        ticket = client.tickets("4419077")[0]
        self.assertEqual(ticket.price, 0.0)
        self.assertEqual(ticket.event_id, 70823021)

    def test_event_display_name_drops_trailing_brackets(self):
        payload = {"status": "0", "result": [
            {"id": 1, "name": "UFC 333 (перенос с 12.10)", "date": "2026-10-24 22:00:00"},
            {"id": 2, "name": "Без даты", "date": "мусор"},
        ]}
        client = AfishaClient("u", "p", "1", opener=canned(payload))
        events = client.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].display_name, "UFC 333")


if __name__ == "__main__":
    unittest.main()
