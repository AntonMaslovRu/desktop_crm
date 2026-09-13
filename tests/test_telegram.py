import json
import unittest

from envo.telegram import Silent, Telegram, rub


class Sending(unittest.TestCase):
    def test_disabled_without_token(self):
        self.assertFalse(Telegram("", "").send("привет"))

    def test_retries_then_gives_up_quietly(self):
        calls = []

        def broken(url, body):
            calls.append(body)
            raise OSError("телеграм молчит")

        tg = Telegram("t", "c", opener=broken, sleep=lambda _: None)
        self.assertFalse(tg.send("x"))
        self.assertEqual(len(calls), 3)

    def test_sends_html_to_chat(self):
        seen = {}

        def ok(url, body):
            seen["url"], seen["body"] = url, body.decode()
            return json.dumps({"ok": True}).encode()

        self.assertTrue(Telegram("tok", "42", opener=ok).send("<b>x</b>"))
        self.assertIn("bottok/sendMessage", seen["url"])
        self.assertIn("chat_id=42", seen["body"])
        self.assertIn("parse_mode=HTML", seen["body"])

    def test_silent_collects(self):
        tg = Silent()
        tg.send("a")
        self.assertEqual(tg.sent, ["a"])

    def test_rub_formatting(self):
        self.assertEqual(rub(126000), "126 000 ₽")
        self.assertEqual(rub(94500.4), "94 500 ₽")
