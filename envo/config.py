"""Настройки из окружения. Секреты — только там, в коде их нет."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"{name} не задан")
    return value or ""


@dataclass(frozen=True)
class Settings:
    db_dsn: str
    afisha_login: str
    afisha_password: str
    afisha_city: str
    telegram_token: str
    telegram_chat: str
    graph_tenant: str = ""
    graph_client_id: str = ""
    mail_from: str = "support@envo.live"
    mail_cache: str = "/var/lib/envo/graph_token.json"
    rate_buffer: float = 1.0
    quiet_from: int = 21  # клиентам не пишем с 21:00 до 09:00 МСК
    quiet_to: int = 9
    digest_hours: tuple[int, ...] = (9, 19)  # сводки по Москве

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            db_dsn=_env("ENVO_DB_DSN", required=True),
            afisha_login=_env("ENVO_AFISHA_LOGIN", required=True),
            afisha_password=_env("ENVO_AFISHA_PASSWORD", required=True),
            afisha_city=_env("ENVO_AFISHA_CITY_ID", required=True),
            telegram_token=_env("ENVO_TELEGRAM_TOKEN"),
            telegram_chat=_env("ENVO_TELEGRAM_CHAT"),
            graph_tenant=_env("ENVO_GRAPH_TENANT"),
            graph_client_id=_env("ENVO_GRAPH_CLIENT_ID"),
            mail_from=_env("ENVO_MAIL_FROM", "support@envo.live"),
            mail_cache=_env("ENVO_MAIL_CACHE", "/var/lib/envo/graph_token.json"),
            rate_buffer=float(_env("ENVO_RATE_BUFFER", "1.0")),
        )
