"""Тесты ингеста идут против настоящего Postgres: проверяем схему, а не её имитацию.

Если базы нет, эти тесты пропускаются, а чистая логика проверяется всё равно.
"""

import os
import uuid

import pytest

ADMIN_DSN = os.environ.get("ENVO_TEST_DSN", "postgresql://envo:envo@127.0.0.1/envo_test")


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    from envo import db

    try:
        connection = psycopg.connect(ADMIN_DSN, row_factory=psycopg.rows.dict_row)
    except psycopg.OperationalError as exc:  # базы нет — не повод валить прогон
        pytest.skip(f"Postgres недоступен: {exc}")

    schema = "t" + uuid.uuid4().hex[:12]
    with connection:
        connection.execute(f"CREATE SCHEMA {schema}")
        connection.execute(f"SET search_path TO {schema}")
        db.apply_schema(connection)
        try:
            yield connection
        finally:
            connection.rollback()
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            connection.commit()
    connection.close()
