"""HTTP API для Mac-приложения. Тонкий слой: авторизация, роуты, JSON. Логика — в модулях."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

import psycopg
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from envo import db, mailer
from envo.api import auth, queries
from envo.config import MSK


class Login(BaseModel):
    login: str
    password: str
    device: str = ""


class LetterAction(BaseModel):
    action: str  # release | hold | skip


class Switch(BaseModel):
    on: bool


class TicketsSent(BaseModel):
    sent: bool


def create_app(dsn: str) -> FastAPI:
    app = FastAPI(title="Envo Desk", version="0.1.0", docs_url="/docs")

    def connection() -> Iterator[psycopg.Connection]:
        with db.connect(dsn) as conn:
            yield conn

    def current_user(authorization: str = Header(default=""),
                     conn: psycopg.Connection = Depends(connection)) -> str:
        token = authorization.removeprefix("Bearer ").strip()
        user = auth.whoami(conn, token) if token else None
        if not user:
            raise HTTPException(401, "нужен вход")
        return user

    @app.post("/auth/login")
    def do_login(body: Login = Body(...), conn: psycopg.Connection = Depends(connection)) -> dict:
        token = auth.login(conn, body.login, body.password, device=body.device)
        if not token:
            raise HTTPException(401, "неверный логин или пароль")
        return {"token": token}

    @app.post("/auth/logout")
    def do_logout(authorization: str = Header(default=""),
                  conn: psycopg.Connection = Depends(connection),
                  user: str = Depends(current_user)) -> dict:
        auth.logout(conn, authorization.removeprefix("Bearer ").strip())
        return {"ok": True}

    @app.get("/me")
    def me(user: str = Depends(current_user)) -> dict:
        return {"login": user}

    @app.get("/overview")
    def overview(period: int = Query(30, ge=1, le=365), conn=Depends(connection),
                 user: str = Depends(current_user)) -> dict:
        return queries.overview(conn, period)

    @app.get("/events")
    def events(conn=Depends(connection), user: str = Depends(current_user)) -> list[dict]:
        rows = db.fetch_all(
            conn,
            "SELECT id, afisha_id, display_name, starts_at, tracking FROM events"
            " WHERE starts_at >= now() - interval '1 day' ORDER BY starts_at",
        )
        return [{"id": r["id"], "afisha_id": r["afisha_id"], "name": r["display_name"],
                 "starts_at": r["starts_at"].isoformat(), "tracking": r["tracking"]} for r in rows]

    @app.get("/events/{event_id}")
    def event(event_id: int, conn=Depends(connection), user: str = Depends(current_user)) -> dict:
        card = queries.event_card(conn, event_id)
        if card is None:
            raise HTTPException(404, "события нет")
        return card

    @app.get("/events/{event_id}/orders")
    def event_orders(event_id: int, before: str | None = None, limit: int = Query(30, ge=1, le=200),
                     conn=Depends(connection), user: str = Depends(current_user)) -> dict:
        return queries.event_orders(conn, event_id, before=before, limit=limit)

    @app.get("/letters")
    def letters(state: str = "queued", conn=Depends(connection),
                user: str = Depends(current_user)) -> list[dict]:
        rows = db.fetch_all(
            conn,
            "SELECT id, kind, ref_id, to_addr, subject, body_html, state, created_at, sent_at,"
            " attempts, last_error FROM letters WHERE state = %s ORDER BY created_at LIMIT 200",
            (state,),
        )
        return [dict(r, created_at=r["created_at"].isoformat(),
                     sent_at=r["sent_at"].isoformat() if r["sent_at"] else None) for r in rows]

    @app.post("/letters/{letter_id}")
    def letter_action(letter_id: int, body: LetterAction = Body(...), conn=Depends(connection),
                      user: str = Depends(current_user)) -> dict:
        target = {"release": "queued", "hold": "held", "skip": "skipped"}.get(body.action)
        if target is None:
            raise HTTPException(400, "действие: release, hold или skip")
        row = db.fetch_one(
            conn,
            "UPDATE letters SET state = %s WHERE id = %s AND state IN ('queued', 'held')"
            " RETURNING id", (target, letter_id),
        )
        if row is None:
            raise HTTPException(409, "письмо уже ушло или его нет")
        db.audit(conn, user, f"письмо {body.action}", {"letter": letter_id})
        return {"ok": True, "state": target}

    @app.post("/mail/hold-all")
    def hold_all(body: Switch = Body(...), conn=Depends(connection), user: str = Depends(current_user)) -> dict:
        conn.execute("INSERT INTO kv (key, value) VALUES ('mail.hold_all', %s)"
                     " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                     ("1" if body.on else "0",))
        db.audit(conn, user, "стоп-кран почты", {"on": body.on})
        return {"ok": True, "on": body.on}

    @app.post("/orders/{order_id}/tickets-sent")
    def tickets_sent(order_id: int, body: TicketsSent = Body(...), conn=Depends(connection),
                     user: str = Depends(current_user)) -> dict:
        """Ручная отметка «билеты отправлены» — для билетов, выданных не PDF."""
        conn.execute(
            "UPDATE orders SET tickets_sent_at = %s, tickets_sent_by = %s WHERE id = %s",
            (datetime.now(MSK) if body.sent else None, "manual" if body.sent else None, order_id),
        )
        db.audit(conn, user, "билеты отправлены" if body.sent else "отметка снята", {"order": order_id})
        return {"ok": True}

    @app.get("/runs")
    def runs(limit: int = Query(50, ge=1, le=500), conn=Depends(connection),
             user: str = Depends(current_user)) -> list[dict]:
        rows = db.fetch_all(
            conn, "SELECT id, job, started_at, finished_at, status, stats, error"
                  " FROM runs ORDER BY started_at DESC LIMIT %s", (limit,),
        )
        return [dict(r, started_at=r["started_at"].isoformat(),
                     finished_at=r["finished_at"].isoformat() if r["finished_at"] else None)
                for r in rows]

    @app.get("/health")
    def health(conn=Depends(connection)) -> dict:
        last = db.fetch_one(conn, "SELECT max(finished_at) AS t FROM runs WHERE status = 'ok'")["t"]
        return {"ok": True, "last_ok_run": last.isoformat() if last else None}

    return app
