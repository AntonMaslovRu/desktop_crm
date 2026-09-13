"""envoctl — точка входа: разовые команды и режим службы с планировщиком.

    envoctl sync            один прогон продаж
    envoctl ladder          пересчёт ступеней
    envoctl digest          сводки, если время пришло
    envoctl check           проверка сети до нужных доменов
    envoctl serve           планировщик: всё по расписанию, пока не остановят
"""

from __future__ import annotations

import logging
import sys
import time
import urllib.request
from datetime import datetime

from envo import db, digest, ingest, ladder
from envo.afisha import AfishaClient
from envo.config import MSK, Settings
from envo.telegram import Telegram

log = logging.getLogger("envo")

DOMAINS = ("api.tickets.yandex.net", "www.cbr.ru", "afisha.yandex.ru",
           "outlook.office365.com", "news.google.com", "api.telegram.org")

PERIODS = {"sales.sync": 600, "ladder.check": 600, "digest": 300, "heartbeat": 900}


def check_network() -> dict[str, str]:
    """Первое, что делает служба на новом сервере: видим ли мы, кого должны."""
    result: dict[str, str] = {}
    for host in DOMAINS:
        try:
            request = urllib.request.Request(f"https://{host}/", method="HEAD",
                                             headers={"User-Agent": "EnvoDesk/0.1"})
            with urllib.request.urlopen(request, timeout=15) as response:
                result[host] = str(response.status)
        except urllib.error.HTTPError as exc:
            result[host] = str(exc.code)  # любой HTTP-код значит «достучались»
        except Exception as exc:  # noqa: BLE001 — сюда попадает и DNS, и таймаут
            result[host] = f"нет: {type(exc).__name__}"
    return result


class App:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self.client = AfishaClient(self.settings.afisha_login, self.settings.afisha_password,
                                   self.settings.afisha_city)
        self.tg = Telegram(self.settings.telegram_token, self.settings.telegram_chat)

    def sync(self) -> None:
        with db.connect(self.settings.db_dsn) as conn:
            stats = ingest.run(conn, self.client)
        if stats.skipped:
            log.info("продажи: прогон уже идёт, пропуск")
            return
        log.info("продажи: %s", stats.as_dict())
        if stats.unknown_statuses or stats.unknown_events:
            self.tg.send("⚠️ Ингест: незнакомые статусы "
                         f"{stats.unknown_statuses or '—'}, события вне каталога "
                         f"{len(stats.unknown_events)}")

    def ladder(self) -> None:
        with db.connect(self.settings.db_dsn) as conn:
            with db.run_record(conn, "ladder.check") as slot:
                slot.update(ladder.run(conn, self.tg))

    def digest(self) -> None:
        with db.connect(self.settings.db_dsn) as conn:
            digest.due(conn, self.tg, self.settings.digest_hours, datetime.now(MSK))

    def heartbeat(self) -> None:
        """Пинг внешнего сторожа. Молчание — сигнал снаружи, а не от упавшей программы."""
        url = __import__("os").environ.get("ENVO_HEARTBEAT_URL")
        if not url:
            return
        try:
            urllib.request.urlopen(url, timeout=10).read()
        except Exception as exc:  # noqa: BLE001
            log.warning("сторож не ответил: %s", exc)

    def serve(self) -> None:
        """Простой планировщик: задача запускается, когда с прошлого успеха прошёл период.

        Пропущенные слоты не догоняются по одному — задача просто выполняется один раз.
        """
        report = check_network()
        bad = [h for h, v in report.items() if v.startswith("нет")]
        log.info("сеть: %s", report)
        if bad:
            self.tg.send("⚠️ Envo Desk стартовал, но не видит: " + ", ".join(bad))
        else:
            self.tg.send("Envo Desk запущен, сеть в порядке.")

        jobs = {"sales.sync": self.sync, "ladder.check": self.ladder,
                "digest": self.digest, "heartbeat": self.heartbeat}
        last: dict[str, float] = {name: 0.0 for name in jobs}
        while True:
            now = time.monotonic()
            for name, fn in jobs.items():
                if now - last[name] < PERIODS[name]:
                    continue
                try:
                    fn()
                    last[name] = time.monotonic()
                except Exception as exc:  # noqa: BLE001 — одна упавшая задача не роняет службу
                    log.exception("%s упал", name)
                    self._alert_once(name, exc)
                    last[name] = time.monotonic() - PERIODS[name] + 120  # повтор через 2 мин
            time.sleep(15)

    _last_alert: dict[str, float] = {}

    def _alert_once(self, job: str, exc: Exception) -> None:
        """Одна и та же ошибка — не чаще раза в час, иначе алерты перестают читать."""
        key = f"{job}:{type(exc).__name__}"
        now = time.monotonic()
        if now - self._last_alert.get(key, -1e9) < 3600:
            return
        self._last_alert[key] = now
        self.tg.send(f"⚠️ <b>{job}</b> упал: {type(exc).__name__}: {str(exc)[:300]}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    argv = argv if argv is not None else sys.argv[1:]
    command = argv[0] if argv else "help"
    if command == "check":
        for host, status in check_network().items():
            print(f"{host:28} {status}")
        return 0
    if command not in {"sync", "ladder", "digest", "serve"}:
        print(__doc__)
        return 0 if command == "help" else 2
    app = App()
    getattr(app, command)()
    return 0


if __name__ == "__main__":
    sys.exit(main())
