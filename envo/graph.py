"""Microsoft Graph: отправка с support@envo.live и поиск в «Отправленных».

Вход — device code при первой настройке, дальше refresh-токен в кэше msal.
Ничего не отправляем без токена: транспорт честно падает, очередь копится.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Send", "Mail.Read"]


class GraphTransport:
    def __init__(
        self,
        tenant: str,
        client_id: str,
        *,
        cache_path: Path,
        opener: Callable[[urllib.request.Request], bytes] | None = None,
        token: str | None = None,
    ) -> None:
        self._tenant = tenant
        self._client_id = client_id
        self._cache_path = cache_path
        self._open = opener or self._fetch
        self._token = token  # для тестов; боевой берётся из msal

    @staticmethod
    def _fetch(request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    # --- авторизация ---

    def _app(self):
        import msal  # тяжёлая зависимость, тянем только здесь

        cache = msal.SerializableTokenCache()
        if self._cache_path.exists():
            cache.deserialize(self._cache_path.read_text())
        app = msal.PublicClientApplication(
            self._client_id, authority=f"https://login.microsoftonline.com/{self._tenant}",
            token_cache=cache,
        )
        return app, cache

    def _save(self, cache) -> None:
        if cache.has_state_changed:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(cache.serialize())
            self._cache_path.chmod(0o600)

    def login(self, prompt: Callable[[str], None] = print) -> None:
        """Первичный вход: код на экране, подтверждение в браузере. Один раз."""
        app, cache = self._app()
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"device flow не запустился: {flow.get('error_description')}")
        prompt(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(result.get("error_description", "вход не удался"))
        self._save(cache)

    def access_token(self) -> str:
        if self._token:
            return self._token
        app, cache = self._app()
        accounts = app.get_accounts()
        result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        self._save(cache)
        if not result or "access_token" not in result:
            raise RuntimeError("нет действующего токена Graph: нужен envoctl mail-login")
        return result["access_token"]

    # --- запросы ---

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            GRAPH + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.access_token()}",
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        raw = self._open(request)
        return json.loads(raw.decode()) if raw else {}

    def send(self, to: str, subject: str, html: str) -> str:
        self._request("POST", "/me/sendMail", {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": True,
        })
        return "sent"  # sendMail не возвращает id; сам факт попал в «Отправленные»

    def already_sent(self, marker: str) -> bool:
        """Страховка от старой рутины: письмо с номером заказа в теме уже уходило?"""
        query = urllib.parse.quote(f'"{marker}"')
        result = self._request(
            "GET", f"/me/mailFolders/sentitems/messages?$search={query}&$top=3&$select=subject"
        )
        return any(marker in (m.get("subject") or "") for m in result.get("value", []))
