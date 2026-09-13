"""Telegram — канал для владельца: алерты и сводки. Клиентам отсюда не пишем."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

RETRIES = 3


class Telegram:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        opener: Callable[[str, bytes], bytes] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat = chat_id
        self._open = opener or self._post
        self._sleep = sleep
        self.enabled = bool(token and chat_id)

    @staticmethod
    def _post(url: str, body: bytes) -> bytes:
        request = urllib.request.Request(url, data=body)
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def send(self, text: str) -> bool:
        """Отправляет HTML-сообщение. Молчащий Telegram не роняет прогон — возвращает False."""
        if not self.enabled:
            return False
        body = urllib.parse.urlencode({
            "chat_id": self._chat,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        for attempt in range(RETRIES):
            try:
                payload = json.loads(self._open(self._url, body).decode())
                return bool(payload.get("ok"))
            except (urllib.error.URLError, OSError, ValueError):
                if attempt < RETRIES - 1:
                    self._sleep(3)
        return False


class Silent(Telegram):
    """Заглушка: копит сообщения вместо отправки. Для тестов и режима «тихо»."""

    def __init__(self) -> None:
        super().__init__("", "")
        self.sent: list[str] = []
        self.enabled = True

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def rub(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"
