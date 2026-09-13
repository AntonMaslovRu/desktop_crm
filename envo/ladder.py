"""Ценовые ступени: наблюдаем, не управляем.

Цену ставит человек в кабинете Афиши. Мы смотрим, по какой цене реально уходят билеты,
считаем проданное по ступеням и говорим, когда квота выбрана. Билет попадает на ступень
по цене (невозвратный) или на открытую в этот момент ступень (возвратный — его цена
всегда выше на надбавку). Всё, что не легло ни на одну ступень, — продажа мимо лестницы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from envo import db
from envo.telegram import Telegram, rub


@dataclass
class Step:
    id: int
    idx: int
    price: float
    quota: int
    sold: int
    closed_at: datetime | None


@dataclass
class LadderState:
    category_id: int
    event_title: str
    category_name: str
    steps: list[Step]
    off_ladder: dict[float, int] = field(default_factory=dict)  # цена → сколько

    @property
    def active(self) -> Step | None:
        return next((s for s in self.steps if s.sold < s.quota), None)

    @property
    def done(self) -> bool:
        return all(s.sold >= s.quota for s in self.steps)


def count_sales(conn: psycopg.Connection, category_id: int, count_from: datetime | None) -> tuple[dict[int, int], dict[float, int]]:
    """Разложить проданные билеты категории по ступеням. Возвращает (по id ступени, мимо)."""
    steps = db.fetch_all(
        conn,
        "SELECT id, idx, price, opened_at FROM steps WHERE category_id = %s ORDER BY idx",
        (category_id,),
    )
    by_price = {float(s["price"]): s for s in steps}
    tickets = db.fetch_all(
        conn,
        """
        SELECT t.price, t.refundable, t.sold_at
          FROM tickets t JOIN orders o ON o.id = t.order_id
         WHERE t.category_id = %s AND t.status = 'sold' AND o.status = 'paid'
           AND (%s::timestamptz IS NULL OR t.sold_at >= %s)
         ORDER BY t.sold_at
        """,
        (category_id, count_from, count_from),
    )
    per_step: dict[int, int] = {s["id"]: 0 for s in steps}
    off: dict[float, int] = {}
    current: dict | None = None
    for t in tickets:
        price = float(t["price"])
        step = by_price.get(price)
        if step is None and t["refundable"] and current is not None:
            step = current  # возвратный уходит на открытую ступень
        if step is None:
            off[price] = off.get(price, 0) + 1
            continue
        per_step[step["id"]] += 1
        if step["opened_at"] is None:
            conn.execute("UPDATE steps SET opened_at = %s WHERE id = %s", (t["sold_at"], step["id"]))
            step["opened_at"] = t["sold_at"]
        if current is None or step["idx"] >= current["idx"]:
            current = step
    return per_step, off


def refresh(conn: psycopg.Connection, category_id: int) -> LadderState:
    """Пересчитать ступени категории по билетам и записать в базу."""
    head = db.fetch_one(
        conn,
        """
        SELECT c.name AS category, e.display_name AS event, e.id AS event_id
          FROM categories c JOIN events e ON e.id = c.event_id
         WHERE c.id = %s
        """,
        (category_id,),
    )
    if head is None:
        raise ValueError(f"категории {category_id} нет")
    count_from = db.fetch_one(
        conn, "SELECT min(opened_at) AS t FROM steps WHERE category_id = %s", (category_id,)
    )["t"]
    per_step, off = count_sales(conn, category_id, count_from)

    steps: list[Step] = []
    for row in db.fetch_all(
        conn,
        "SELECT id, idx, price, quota, closed_at FROM steps WHERE category_id = %s ORDER BY idx",
        (category_id,),
    ):
        sold = per_step.get(row["id"], 0)
        conn.execute("UPDATE steps SET sold = %s WHERE id = %s", (sold, row["id"]))
        steps.append(Step(row["id"], row["idx"], float(row["price"]), row["quota"], sold,
                          row["closed_at"]))
    return LadderState(category_id, head["event"], head["category"], steps, off)


def close_steps(conn: psycopg.Connection, state: LadderState, tg: Telegram) -> list[str]:
    """Закрывает выбранные ступени и шлёт сигнал. Каждая ступень закрывается один раз."""
    alerts: list[str] = []
    for i, step in enumerate(state.steps):
        if step.sold < step.quota or step.closed_at is not None:
            continue
        conn.execute("UPDATE steps SET closed_at = now() WHERE id = %s", (step.id,))
        step.closed_at = datetime.now()
        nxt = state.steps[i + 1] if i + 1 < len(state.steps) else None
        if nxt:
            text = (f"🔔 <b>{state.event_title} · {state.category_name}</b>\n"
                    f"Ступень {step.idx} по {rub(step.price)} закрыта: продано {step.sold} из {step.quota}.\n"
                    f"Ставь следующую: <b>{rub(nxt.price)}</b> у нас, "
                    f"<b>{rub(nxt.price / 0.9)}</b> на витрине (квота {nxt.quota}).")
        else:
            text = (f"🔔 <b>{state.event_title} · {state.category_name}</b>\n"
                    f"Последняя ступень закрыта. Категория распродана.")
        db.publish(conn, "step.closed", {"category": state.category_id, "step": step.idx,
                                         "next_price": nxt.price if nxt else None})
        tg.send(text)
        alerts.append(text)
    return alerts


def report_off_ladder(conn: psycopg.Connection, state: LadderState, tg: Telegram) -> str | None:
    """Продажи мимо лестницы: сообщаем, только если их стало больше, чем в прошлый раз."""
    key = f"ladder:{state.category_id}:off"
    total = sum(state.off_ladder.values())
    row = db.fetch_one(conn, "SELECT value FROM kv WHERE key = %s", (key,))
    seen = int(row["value"]) if row else 0
    if total <= seen:
        return None
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, str(total)),
    )
    lines = ", ".join(f"{n} шт по {rub(p)}" for p, n in sorted(state.off_ladder.items()))
    text = f"⚠️ <b>{state.event_title} · {state.category_name}</b>\nПродажи мимо лестницы: {lines}"
    tg.send(text)
    return text


def summary(state: LadderState) -> str:
    lines = [f"<b>{state.event_title} · {state.category_name}</b>"]
    for s in state.steps:
        mark = "✅" if s.sold >= s.quota else "▫️"
        lines.append(f"{mark} Ступень {s.idx} по {rub(s.price)}: <b>{s.sold} из {s.quota}</b>")
    active = state.active
    if active:
        lines.append(f"\nСейчас в продаже: ступень {active.idx}, {rub(active.price)} у нас, "
                     f"{rub(active.price / 0.9)} на витрине.")
    return "\n".join(lines)


def run(conn: psycopg.Connection, tg: Telegram) -> dict:
    """Все активные лестницы: пересчёт, закрытия, продажи мимо, сводка при изменениях."""
    stats = {"ladders": 0, "closed": 0, "off_ladder": 0}
    categories = db.fetch_all(
        conn,
        """
        SELECT DISTINCT s.category_id
          FROM steps s JOIN categories c ON c.id = s.category_id
          JOIN events e ON e.id = c.event_id
         WHERE e.tracking AND e.starts_at >= now() - interval '1 day'
        """,
    )
    for row in categories:
        stats["ladders"] += 1
        before = {s["id"]: s["sold"] for s in db.fetch_all(
            conn, "SELECT id, sold FROM steps WHERE category_id = %s", (row["category_id"],))}
        state = refresh(conn, row["category_id"])
        closed = close_steps(conn, state, tg)
        stats["closed"] += len(closed)
        if report_off_ladder(conn, state, tg):
            stats["off_ladder"] += 1
        changed = any(before.get(s.id) != s.sold for s in state.steps)
        if changed and not closed and not state.done:
            tg.send(summary(state))
    return stats
