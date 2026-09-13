"""API: вход, защита роутов, обзор и карточка на реальных данных в базе."""

import uuid

import pytest
from fastapi.testclient import TestClient

from envo import db
from envo.api import auth
from envo.api.app import create_app

from tests.conftest import ADMIN_DSN


@pytest.fixture
def client(conn, monkeypatch):
    """Приложение ходит в ту же схему, что и тест: search_path через переменную соединения."""
    schema = db.fetch_one(conn, "SHOW search_path")["search_path"]
    conn.commit()  # схема должна быть видна другим соединениям
    dsn = f"{ADMIN_DSN}?options=-csearch_path%3D{schema}"
    app = create_app(dsn)
    auth.create_user(conn, "anton", "correct horse battery")
    conn.commit()
    with TestClient(app) as c:
        yield c


def login(client, password="correct horse battery"):
    r = client.post("/auth/login", json={"login": "anton", "password": password})
    return r


class TestAuth:
    def test_login_and_me(self, client):
        token = login(client).json()["token"]
        r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json() == {"login": "anton"}

    def test_wrong_password(self, client):
        assert login(client, "wrong").status_code == 401

    def test_routes_need_token(self, client):
        assert client.get("/overview").status_code == 401
        assert client.get("/overview", headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_logout_kills_token(self, client):
        token = login(client).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        assert client.post("/auth/logout", headers=h).status_code == 200
        assert client.get("/me", headers=h).status_code == 401

    def test_lockout_after_five_failures(self, client):
        auth._fails.clear()
        for _ in range(5):
            login(client, "wrong")
        assert login(client).status_code == 401, "верный пароль после пяти промахов тоже ждёт"
        auth._fails.clear()


class TestScreens:
    def test_overview_shape(self, client):
        h = {"Authorization": f"Bearer {login(client).json()['token']}"}
        r = client.get("/overview?period=7", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"kpis", "daily", "sell_through", "attention", "recent"}
        assert body["kpis"]["tickets"] == 0
        assert any(a["kind"] == "silence" for a in body["attention"]), "прогонов не было — это тревога"

    def test_event_card_and_orders(self, client, conn):
        event = db.fetch_one(conn, "INSERT INTO events (title, display_name, starts_at) VALUES"
                                   " ('UFC 333','UFC 333','2026-10-24 18:00') RETURNING id")["id"]
        contact = db.fetch_one(conn, "INSERT INTO contacts (name, email) VALUES ('Петров Иван','a@b.ru') RETURNING id")["id"]
        order = db.fetch_one(conn, "INSERT INTO orders (afisha_id, event_id, contact_id, status, ordered_at, total, tickets_count)"
                                   " VALUES ('4419077', %s, %s, 'paid', now(), 108000, 1) RETURNING id", (event, contact))["id"]
        conn.execute("INSERT INTO tickets (order_id, afisha_id, sector, price) VALUES (%s, 't1', 'Кат. 6', 108000)", (order,))
        conn.commit()
        h = {"Authorization": f"Bearer {login(client).json()['token']}"}

        card = client.get(f"/events/{event}", headers=h).json()
        assert card["pnl"]["revenue"] == 108000 and card["pnl"]["tickets_sold"] == 1
        assert card["log"][0]["who"] == "Петров Иван"

        orders = client.get(f"/events/{event}/orders", headers=h).json()
        assert orders["orders"][0]["order"] == "4419077" and orders["next_before"] is None

        assert client.get("/events/999999", headers=h).status_code == 404

    def test_letter_actions_and_kill_switch(self, client, conn):
        conn.execute("INSERT INTO letters (kind, ref_id, to_addr, subject, body_html) VALUES"
                     " ('welcome','1','a@b.ru','s','<p>x</p>')")
        conn.commit()
        h = {"Authorization": f"Bearer {login(client).json()['token']}"}
        letter = client.get("/letters", headers=h).json()[0]
        assert client.post(f"/letters/{letter['id']}", json={"action": "hold"}, headers=h).json()["state"] == "held"
        assert client.get("/letters?state=held", headers=h).json()[0]["id"] == letter["id"]
        assert client.post(f"/letters/{letter['id']}", json={"action": "skip"}, headers=h).status_code == 200
        assert client.post(f"/letters/{letter['id']}", json={"action": "release"}, headers=h).status_code == 409

        assert client.post("/mail/hold-all", json={"on": True}, headers=h).json()["on"] is True
        assert client.get("/overview", headers=h).json()["mail_hold_all"] is True

    def test_health_is_public(self, client):
        assert client.get("/health").status_code == 200
