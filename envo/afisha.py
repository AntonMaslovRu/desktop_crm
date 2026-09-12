"""Клиент CRM Яндекс.Афиши.

Подпись запроса: auth = login:sha1(md5(пароль) + метка времени):метка времени.
API отдаёт то объект, то массив, то массив массивов — нормализуем на входе, чтобы
остальной код видел один формат. Только чтение: писать в Афишу нечем и незачем.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

BASE_URL = "https://api.tickets.yandex.net/api/crm/"
TIMEOUT = 90
RETRIES = 3
USER_AGENT = "EnvoDesk/0.1 (+support@envo.live)"


class AfishaError(RuntimeError):
    """CRM ответила ошибкой или не ответила вовсе."""


def _auth(login: str, password: str, now: Callable[[], float] = time.time) -> str:
    ts = str(int(now()))
    md5 = hashlib.md5(password.encode()).hexdigest()
    return f"{login}:{hashlib.sha1((md5 + ts).encode()).hexdigest()}:{ts}"


def flatten(result: Any) -> list[dict]:
    """Приводит ответ к списку словарей, каким бы он ни пришёл."""
    if result is None:
        return []
    if isinstance(result, dict):
        return [result]
    out: list[dict] = []
    for item in result:
        if isinstance(item, list):
            out.extend(x for x in item if isinstance(x, dict))
        elif isinstance(item, dict):
            out.append(item)
    return out


@dataclass(frozen=True)
class Event:
    id: int
    name: str
    starts_at: datetime

    @property
    def display_name(self) -> str:
        """Имя без хвостового уточнения в скобках — его добавляет Афиша."""
        import re

        return re.sub(r"\s*\([^)]*\)\s*$", "", self.name).strip()


@dataclass(frozen=True)
class Ticket:
    id: str
    order_id: str
    event_id: int
    sector: str
    price: float
    barcode: str


@dataclass(frozen=True)
class Order:
    id: str
    status: int
    is_returned: bool
    ordered_at: datetime | None
    total: float
    tickets_count: int
    customer_name: str
    email: str
    phone: str
    agent_id: int | None
    raw: dict

    @property
    def is_paid(self) -> bool:
        return self.status == 1 and not self.is_returned

    @property
    def is_cart(self) -> bool:
        """Незавершённый заказ — брошенная корзина."""
        return self.status == 0 and not self.is_returned


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class AfishaClient:
    """Тонкий клиент: подпись, ретраи, нормализация. Никакой бизнес-логики."""

    def __init__(
        self,
        login: str,
        password: str,
        city_id: str,
        *,
        opener: Callable[[str], bytes] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._login = login
        self._password = password
        self._city_id = city_id
        self._open = opener or self._fetch
        self._sleep = sleep

    @staticmethod
    def _fetch(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()

    def call(self, action: str, **params: Any) -> list[dict]:
        query: dict[str, Any] = {"action": action, "auth": _auth(self._login, self._password)}
        if action != "crm.city.list":
            query["city_id"] = self._city_id
        query.update({k: v for k, v in params.items() if v is not None})
        url = BASE_URL + "?" + urllib.parse.urlencode(query)

        last: Exception | None = None
        for attempt in range(RETRIES):
            try:
                payload = json.loads(self._open(url).decode())
                if str(payload.get("status")) != "0":
                    raise AfishaError(f"{action}: {payload.get('error')}")
                return flatten(payload.get("result"))
            except AfishaError:
                raise
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
                if attempt < RETRIES - 1:
                    self._sleep(2 * (attempt + 1))
        raise AfishaError(f"{action}: нет ответа после {RETRIES} попыток") from last

    # --- методы, которые нам нужны ---

    def events(self) -> list[Event]:
        out = []
        for row in self.call("crm.event.list"):
            starts_at = _parse_dt(row.get("date"))
            if starts_at is None:
                continue
            out.append(Event(id=int(row["id"]), name=(row.get("name") or "").strip(), starts_at=starts_at))
        return out

    def agents(self) -> dict[int, str]:
        return {int(a["id"]): a.get("name", "") for a in self.call("crm.agent.list") if a.get("id")}

    def orders(self, start: date, end: date) -> list[Order]:
        rows = self.call("crm.order.list", start_date=start.isoformat(), end_date=end.isoformat())
        return [self._order(row) for row in rows]

    def tickets(self, order_id: str) -> list[Ticket]:
        rows = self.call("crm.order.info", order_id=order_id)
        info = rows[0] if rows else {}
        out = []
        for t in info.get("tickets") or []:
            if not t.get("id"):
                continue
            try:
                price = float(t.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            out.append(
                Ticket(
                    id=str(t["id"]),
                    order_id=str(order_id),
                    event_id=int(t.get("event_id") or 0),
                    sector=(t.get("sector") or "").strip(),
                    price=price,
                    barcode=str(t.get("barcode") or ""),
                )
            )
        return out

    @staticmethod
    def _order(row: dict) -> Order:
        customer = row.get("customer") or {}
        try:
            total = float(row.get("sum") or 0)
        except (TypeError, ValueError):
            total = 0.0
        return Order(
            id=str(row["id"]),
            status=int(row.get("status") or 0),
            is_returned=bool(row.get("is_returned")),
            ordered_at=_parse_dt(row.get("order_date")),
            total=total,
            tickets_count=int(row.get("tickets_count") or 0),
            customer_name=(customer.get("name") or "").strip(),
            email=(customer.get("email") or "").strip().lower(),
            phone=(customer.get("phone") or "").lstrip("+"),
            agent_id=int(row["agent_id"]) if row.get("agent_id") else None,
            raw=row,
        )
