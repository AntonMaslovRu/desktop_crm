import json
import unittest
from pathlib import Path

from envo.graph import GraphTransport


class Requests(unittest.TestCase):
    def transport(self, responses):
        seen = []

        def opener(request):
            seen.append((request.get_method(), request.full_url, request.data))
            return json.dumps(responses.pop(0)).encode()

        t = GraphTransport("tenant", "client", mailbox="support@envo.live",
                           cache_path=Path("/nonexistent"), opener=opener, token="tok")
        return t, seen

    ME = {"mail": "support@envo.live"}

    def test_send_posts_html_and_saves_to_sent_items(self):
        t, seen = self.transport([self.ME, {}])
        t.send("a@b.ru", "Тема", "<b>x</b>")
        method, url, data = seen[1]
        body = json.loads(data)
        self.assertEqual((method, url),
                         ("POST", "https://graph.microsoft.com/v1.0/users/support@envo.live/sendMail"))
        self.assertEqual(body["message"]["toRecipients"][0]["emailAddress"]["address"], "a@b.ru")
        self.assertEqual(body["message"]["body"]["contentType"], "HTML")
        self.assertTrue(body["saveToSentItems"])

    def test_already_sent_searches_sent_items_by_order_number(self):
        t, seen = self.transport([{"value": [{"subject": "UFC 333. Заказ: 4419077"}]}])
        self.assertTrue(t.already_sent("4419077"))
        self.assertIn("/users/support@envo.live/mailFolders/sentitems/messages", seen[0][1])
        self.assertIn("4419077", seen[0][1])

    def test_unrelated_hit_is_not_a_duplicate(self):
        t, _ = self.transport([{"value": [{"subject": "Другое письмо"}]}])
        self.assertFalse(t.already_sent("4419077"))

    def test_no_token_is_a_clear_error(self):
        t = GraphTransport("tenant", "client", mailbox="support@envo.live",
                           cache_path=Path("/nonexistent"))
        with self.assertRaises(Exception):
            t.access_token()

    def test_send_refuses_a_foreign_account(self):
        """Токен личного ящика — письма не уходят. Именно так ушли дубли 14.09."""
        t, seen = self.transport([{"mail": "anton@personal.ru"}, {}])
        with self.assertRaises(RuntimeError):
            t.send("a@b.ru", "Тема", "<b>x</b>")
        self.assertEqual(len(seen), 1, "до sendMail дело не дошло")

    def test_mailbox_is_required(self):
        with self.assertRaises(ValueError):
            GraphTransport("tenant", "client", mailbox="", cache_path=Path("/x"))
