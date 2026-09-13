"""Запросы для экранов. Экран — это набор чисел из базы, а не логика."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import psycopg

from envo import db, money
from envo.config import MSK


def _period_start(period: int, now: datetime) -> datetime:
    if period <= 1:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - timedelta(days=period)).replace(hour=0, minute=0, second=0, microsecond=0)


def category_costs(conn: psycopg.Connection) -> dict[int, float]:
    """Средневзвешенная себестоимость по категориям из строк покупок."""
    rows = db.fetch_all(
        conn,
        "SELECT category_id, sum(qty * unit_cost_rub) / sum(qty) AS cost"
        " FROM purchase_lines WHERE category_id IS NOT NULL GROUP BY category_id",
    )
    return {r["category_id"]: float(r["cost"]) for r in rows}


def kpis(conn: psycopg.Connection, period: int, now: datetime) -> dict:
    since = _period_start(period, now)
    costs = category_costs(conn)
    rows = db.fetch_all(
        conn,
        """
        SELECT o.channel, t.price, t.category_id
          FROM tickets t JOIN orders o ON o.id = t.order_id
         WHERE o.status = 'paid' AND t.status = 'sold' AND o.ordered_at >= %s
        """,
        (since,),
    )
    revenue = taxes = net = cost = 0.0
    priced = 0
    for r in rows:
        ch = money.CHANNELS[r["channel"]]
        price = float(r["price"])
        revenue += price
        taxes += price * ch.tax_share
        net += price * ch.net_share
        if r["category_id"] in costs:
            cost += costs[r["category_id"]]
            priced += 1
    return {
        "period_days": period,
        "revenue": revenue,
        "tickets": len(rows),
        "taxes": taxes,
        "profit": net - cost,
        "profit_is_before_cost": priced < len(rows),  # не у всех билетов есть себестоимость
        "tickets_without_cost": len(rows) - priced,
    }


def daily(conn: psycopg.Connection, period: int, now: datetime) -> dict:
    """Выручка по дням стопкой: три главных события плюс «прочее»."""
    since = _period_start(max(period, 1), now)
    rows = db.fetch_all(
        conn,
        """
        SELECT (o.ordered_at AT TIME ZONE 'Europe/Moscow')::date AS day,
               coalesce(e.display_name, 'прочее') AS event, sum(t.price) AS revenue, count(*) AS n
          FROM tickets t JOIN orders o ON o.id = t.order_id LEFT JOIN events e ON e.id = o.event_id
         WHERE o.status = 'paid' AND t.status = 'sold' AND o.ordered_at >= %s
         GROUP BY 1, 2 ORDER BY 1
        """,
        (since,),
    )
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["event"]] = totals.get(r["event"], 0) + float(r["revenue"])
    top = [k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:3]]
    days: dict[date, dict] = {}
    for r in rows:
        key = r["event"] if r["event"] in top else "прочее"
        slot = days.setdefault(r["day"], {"day": r["day"].isoformat(), "tickets": 0, "by": {}})
        slot["tickets"] += r["n"]
        slot["by"][key] = slot["by"].get(key, 0) + float(r["revenue"])
    return {"series": top + (["прочее"] if any("прочее" in d["by"] for d in days.values()) else []),
            "days": [days[d] for d in sorted(days)]}


def sell_through(conn: psycopg.Connection, now: datetime) -> list[dict]:
    """Успеваем ли распродать: продано из закупленного против прошедшей доли срока."""
    rows = db.fetch_all(
        conn,
        """
        SELECT e.id, e.display_name, e.starts_at,
               coalesce((SELECT sum(qty) FROM purchase_lines pl WHERE pl.event_id = e.id), 0) AS bought,
               coalesce((SELECT min(p.paid_at) FROM purchase_lines pl JOIN purchases p ON p.id = pl.purchase_id
                         WHERE pl.event_id = e.id), NULL) AS first_buy,
               (SELECT count(*) FROM tickets t JOIN orders o ON o.id = t.order_id
                 WHERE o.event_id = e.id AND o.status = 'paid' AND t.status = 'sold') AS sold,
               (SELECT min(o.ordered_at) FROM orders o WHERE o.event_id = e.id AND o.status = 'paid') AS first_sale
          FROM events e
         WHERE e.tracking AND e.starts_at >= %s
         ORDER BY e.starts_at
        """,
        (now.replace(tzinfo=None),),
    )
    out = []
    for r in rows:
        stock = int(r["bought"]) or int(r["sold"])  # без закупки считаем от проданного
        start = r["first_buy"] or (r["first_sale"].date() if r["first_sale"] else None)
        elapsed = None
        if start and r["starts_at"]:
            total = (r["starts_at"].date() - start).days or 1
            elapsed = max(0.0, min(1.0, (now.date() - start).days / total))
        out.append({
            "event_id": r["id"], "event": r["display_name"],
            "starts_at": r["starts_at"].isoformat(),
            "days_left": (r["starts_at"].date() - now.date()).days,
            "bought": int(r["bought"]), "sold": int(r["sold"]), "left": max(0, stock - int(r["sold"])),
            "sold_share": (int(r["sold"]) / stock) if stock else None,
            "elapsed_share": elapsed,
        })
    return out


def attention(conn: psycopg.Connection, now: datetime) -> list[dict]:
    items: list[dict] = []
    last = db.fetch_one(
        conn, "SELECT max(finished_at) AS t FROM runs WHERE job = 'sales.sync' AND status = 'ok'"
    )["t"]
    if last is None or now - last > timedelta(minutes=30):
        ago = f"{int((now - last).total_seconds() // 60)} мин" if last else "никогда"
        items.append({"level": "critical", "kind": "silence",
                      "title": "Прогон продаж молчит", "detail": f"последний успешный: {ago}"})
    closed = db.fetch_all(
        conn,
        "SELECT payload FROM events_log WHERE topic = 'step.closed' AND created_at >= %s ORDER BY id DESC",
        (now - timedelta(days=1),),
    )
    for c in closed:
        p = c["payload"]
        nxt = f"поставить {money.nice_up(p['next_price'] / 0.9):,} ₽ на витрине".replace(",", " ") \
            if p.get("next_price") else "категория распродана"
        items.append({"level": "warning", "kind": "step", "title": f"Ступень {p['step']} закрыта",
                      "detail": nxt, "category_id": p["category"]})
    q = db.fetch_one(conn, "SELECT count(*) FILTER (WHERE state='queued') AS q,"
                           " count(*) FILTER (WHERE state='held') AS h,"
                           " count(*) FILTER (WHERE state='failed') AS f FROM letters")
    if q["f"]:
        items.append({"level": "critical", "kind": "mail", "title": f"{q['f']} писем не ушли",
                      "detail": "пять неудач подряд, смотри очередь"})
    if q["q"] or q["h"]:
        items.append({"level": "info", "kind": "mail", "title": f"{q['q'] + q['h']} писем в очереди",
                      "detail": f"ждут отправки: {q['q']}, удержано: {q['h']}"})
    carts = db.fetch_one(conn, "SELECT count(*) AS n FROM cart_followups WHERE state = 'ready'")["n"]
    if carts:
        items.append({"level": "info", "kind": "cart", "title": f"{carts} корзин готовы к догону",
                      "detail": "письма в очереди"})
    refunds = db.fetch_all(
        conn,
        "SELECT o.afisha_id, e.display_name FROM orders o LEFT JOIN events e ON e.id = o.event_id"
        " WHERE o.status = 'refund' AND o.last_seen_at >= %s", (now - timedelta(days=1),),
    )
    for r in refunds:
        items.append({"level": "warning", "kind": "refund", "title": f"Возврат: {r['display_name'] or '—'}",
                      "detail": f"заказ {r['afisha_id']}"})
    unknown = db.fetch_all(
        conn,
        """
        SELECT DISTINCT e.display_name, t.sector
          FROM tickets t JOIN orders o ON o.id = t.order_id JOIN events e ON e.id = o.event_id
         WHERE t.category_id IS NULL AND e.tracking AND e.starts_at >= %s
        """,
        (now.replace(tzinfo=None),),
    )
    for u in unknown:
        items.append({"level": "info", "kind": "category", "title": "Новая категория",
                      "detail": f"{u['display_name']}: «{u['sector']}» — нужен алиас"})
    no_text = db.fetch_all(
        conn,
        "SELECT display_name FROM events WHERE tracking AND starts_at >= %s"
        " AND letter_single IS NULL", (now.replace(tzinfo=None),),
    )
    if no_text:
        items.append({"level": "info", "kind": "letter",
                      "title": "Нет текста вэлкома",
                      "detail": ", ".join(r["display_name"] for r in no_text)})
    return items


def recent_sales(conn: psycopg.Connection, limit: int = 10) -> list[dict]:
    rows = db.fetch_all(
        conn,
        """
        SELECT o.afisha_id, o.ordered_at, o.status, o.total, o.tickets_count,
               coalesce(e.display_name, '—') AS event, c.name AS buyer,
               (SELECT string_agg(DISTINCT t.sector, ', ') FROM tickets t WHERE t.order_id = o.id) AS sectors
          FROM orders o LEFT JOIN events e ON e.id = o.event_id LEFT JOIN contacts c ON c.id = o.contact_id
         WHERE o.status IN ('paid', 'refund')
         ORDER BY o.ordered_at DESC NULLS LAST LIMIT %s
        """,
        (limit,),
    )
    return [{
        "order": r["afisha_id"], "at": r["ordered_at"].astimezone(MSK).isoformat() if r["ordered_at"] else None,
        "event": r["event"], "sector": r["sectors"] or "", "tickets": r["tickets_count"],
        "total": float(r["total"]), "buyer": r["buyer"] or "без имени", "refund": r["status"] == "refund",
    } for r in rows]


def overview(conn: psycopg.Connection, period: int = 30, now: datetime | None = None) -> dict:
    now = now or datetime.now(MSK)
    return {
        "now": now.isoformat(),
        "kpis": kpis(conn, period, now),
        "daily": daily(conn, period, now),
        "sell_through": sell_through(conn, now),
        "attention": attention(conn, now),
        "recent": recent_sales(conn),
        "mail_hold_all": bool(db.fetch_one(conn, "SELECT value FROM kv WHERE key='mail.hold_all'")
                              and db.fetch_one(conn, "SELECT value FROM kv WHERE key='mail.hold_all'")["value"] == "1"),
    }


def event_card(conn: psycopg.Connection, event_id: int, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(MSK)
    head = db.fetch_one(
        conn,
        "SELECT id, afisha_id, title, display_name, starts_at, tracking, letter_single, letter_multi"
        " FROM events WHERE id = %s", (event_id,),
    )
    if head is None:
        return None
    costs = category_costs(conn)
    cats = db.fetch_all(
        conn,
        """
        SELECT c.id, c.name,
               coalesce((SELECT sum(qty) FROM purchase_lines pl WHERE pl.category_id = c.id), 0) AS bought,
               (SELECT count(*) FROM tickets t JOIN orders o ON o.id = t.order_id
                 WHERE t.category_id = c.id AND o.status='paid' AND t.status='sold') AS sold,
               (SELECT max(t.price) FROM tickets t JOIN orders o ON o.id = t.order_id
                 WHERE t.category_id = c.id AND o.status='paid' AND t.refundable IS NOT TRUE
                 AND o.ordered_at = (SELECT max(o2.ordered_at) FROM tickets t2 JOIN orders o2 ON o2.id=t2.order_id
                                     WHERE t2.category_id = c.id AND o2.status='paid')) AS last_price
          FROM categories c WHERE c.event_id = %s ORDER BY c.name
        """,
        (event_id,),
    )
    categories = []
    for c in cats:
        steps = db.fetch_all(conn, "SELECT idx, price, quota, sold, closed_at FROM steps"
                                   " WHERE category_id = %s ORDER BY idx", (c["id"],))
        cost = costs.get(c["id"])
        price = float(c["last_price"]) if c["last_price"] else None
        categories.append({
            "id": c["id"], "name": c["name"], "bought": int(c["bought"]), "sold": int(c["sold"]),
            "left": max(0, int(c["bought"]) - int(c["sold"])), "cost": cost,
            "current_price": price,
            "current_margin": money.margin_at_price(price, cost) if price and cost else None,
            "steps": [{"idx": s["idx"], "price": float(s["price"]), "quota": s["quota"],
                       "sold": s["sold"], "closed": s["closed_at"] is not None} for s in steps],
        })

    sales = db.fetch_all(
        conn,
        "SELECT t.price, o.channel, t.category_id FROM tickets t JOIN orders o ON o.id = t.order_id"
        " WHERE o.event_id = %s AND o.status = 'paid' AND t.status = 'sold'", (event_id,),
    )
    invested = db.fetch_one(conn, "SELECT coalesce(sum(qty * unit_cost_rub), 0) AS v"
                                  " FROM purchase_lines WHERE event_id = %s", (event_id,))["v"]
    cost_of_sold = sum(costs.get(s["category_id"], 0.0) for s in sales)
    pnl = money.event_pnl([(float(s["price"]), s["channel"]) for s in sales],
                          invested=float(invested), cost_of_sold=cost_of_sold)

    log = db.fetch_all(
        conn,
        """
        SELECT o.ordered_at AS at, o.status AS kind, o.afisha_id AS ref, o.total, o.tickets_count,
               c.name AS who
          FROM orders o LEFT JOIN contacts c ON c.id = o.contact_id
         WHERE o.event_id = %s AND o.status IN ('paid', 'refund')
         ORDER BY o.ordered_at DESC LIMIT 50
        """,
        (event_id,),
    )
    facts = db.fetch_all(conn, "SELECT field, value, source, source_url, updated_at"
                               " FROM event_facts WHERE event_id = %s ORDER BY field", (event_id,))
    return {
        "id": head["id"], "afisha_id": head["afisha_id"], "title": head["title"],
        "display_name": head["display_name"], "starts_at": head["starts_at"].isoformat(),
        "days_left": (head["starts_at"].date() - now.date()).days, "tracking": head["tracking"],
        "has_letter": bool(head["letter_single"]),
        "pnl": {"invested": pnl.invested, "revenue": pnl.revenue, "profit": pnl.profit,
                "taxes": pnl.taxes, "platform": pnl.platform, "frozen": pnl.frozen,
                "tickets_sold": len(sales)},
        "categories": categories,
        "log": [{"at": r["at"].astimezone(MSK).isoformat() if r["at"] else None, "kind": r["kind"],
                 "ref": r["ref"], "total": float(r["total"]), "tickets": r["tickets_count"],
                 "who": r["who"]} for r in log],
        "facts": [{"field": f["field"], "value": f["value"], "source": f["source"],
                   "url": f["source_url"]} for f in facts],
    }


def event_orders(conn: psycopg.Connection, event_id: int, *, before: str | None, limit: int = 30) -> dict:
    """Бесконечная лента заказов события: от последнего к первому, курсор — время заказа."""
    rows = db.fetch_all(
        conn,
        """
        SELECT o.id, o.afisha_id, o.ordered_at, o.status, o.channel, o.total, o.tickets_count,
               o.welcome_sent_at, o.tickets_sent_at, o.tickets_sent_by,
               c.id AS contact_id, c.name AS buyer, c.email,
               (SELECT string_agg(DISTINCT t.sector, ', ') FROM tickets t WHERE t.order_id = o.id) AS sectors,
               (SELECT count(*) FROM letters l WHERE l.contact_id = c.id AND l.state = 'sent') AS letters
          FROM orders o LEFT JOIN contacts c ON c.id = o.contact_id
         WHERE o.event_id = %s AND o.status IN ('paid', 'refund')
           AND (%s::timestamptz IS NULL OR o.ordered_at < %s)
         ORDER BY o.ordered_at DESC LIMIT %s
        """,
        (event_id, before, before, limit + 1),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "orders": [{
            "id": r["id"], "order": r["afisha_id"],
            "at": r["ordered_at"].astimezone(MSK).isoformat() if r["ordered_at"] else None,
            "status": r["status"], "channel": r["channel"], "total": float(r["total"]),
            "tickets": r["tickets_count"], "sector": r["sectors"] or "",
            "buyer": r["buyer"] or "без имени", "contact_id": r["contact_id"],
            "welcome_sent_at": r["welcome_sent_at"].isoformat() if r["welcome_sent_at"] else None,
            "tickets_sent_at": r["tickets_sent_at"].isoformat() if r["tickets_sent_at"] else None,
            "tickets_sent_by": r["tickets_sent_by"], "letters": r["letters"],
        } for r in rows],
        "next_before": rows[-1]["ordered_at"].isoformat() if more and rows[-1]["ordered_at"] else None,
    }
