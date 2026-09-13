from pathlib import Path

from envo import db, seed

SEED = Path(__file__).resolve().parent.parent / "deploy" / "seed"


class TestSeed:
    def test_reference_creates_stub_and_keeps_texts(self, conn):
        first = seed.run(conn, SEED)
        assert first["events"] >= 5
        row = db.fetch_one(conn, "SELECT display_name, letter_single FROM events WHERE afisha_id = 70823021")
        assert row["display_name"].startswith("UFC 333") and row["letter_single"]

    def test_ladder_and_backfill(self, conn):
        seed.run(conn, SEED)
        event = db.fetch_one(conn, "SELECT id FROM events WHERE afisha_id = 70823021")["id"]
        order = db.fetch_one(conn, "INSERT INTO orders (afisha_id, event_id, status) VALUES ('1', %s, 'paid') RETURNING id", (event,))["id"]
        conn.execute("INSERT INTO tickets (order_id, afisha_id, sector, price) VALUES (%s, 't1', 'Верхний ярус секторов со 101 до 115 (Невозвратные)', 94500)", (order,))
        seed.run(conn, SEED)
        assert db.fetch_one(conn, "SELECT category_id FROM tickets")["category_id"] is not None
        steps = db.fetch_all(conn, "SELECT idx, price, quota, opened_at FROM steps ORDER BY idx")
        assert [(s["idx"], float(s["price"]), s["quota"]) for s in steps] == [(1, 94500.0, 7), (2, 108000.0, 17), (3, 126000.0, 9)]
        assert steps[0]["opened_at"] is not None

    def test_sync_does_not_overwrite_display_name(self, conn):
        from datetime import datetime
        from envo.afisha import Event
        from envo.ingest import IngestStats, sync_events

        seed.run(conn, SEED)
        sync_events(conn, [Event(70823021, "UFC 333: Волкановски vs Евлоев (перенос)", datetime(2026, 10, 24, 18))], IngestStats())
        row = db.fetch_one(conn, "SELECT display_name, starts_at FROM events WHERE afisha_id = 70823021")
        assert row["display_name"] == "UFC 333: Волкановски vs Евлоев"
        assert row["starts_at"].year == 2026
