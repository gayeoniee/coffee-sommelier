import logging
import os

import psycopg
import pytest

from pipeline.db import apply_schema, reset_tables

TEST_URL = os.getenv("DATABASE_URL_TEST", "postgresql://coffee:coffee@localhost:5432/coffee_test")


@pytest.fixture
def db_conn():
    admin_url = TEST_URL.rsplit("/", 1)[0] + "/coffee"
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    with admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'coffee_test'").fetchone():
            admin.execute("CREATE DATABASE coffee_test")
    conn = psycopg.connect(TEST_URL)
    apply_schema(conn)
    reset_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def caplog(caplog):
    """pytest's caplog, also attached to the "telemetry" logger: `telemetry.configure()` (called by create_app)
    turns propagation off so production lines go only to stdout, which would otherwise hide them from caplog."""
    lg = logging.getLogger("telemetry")
    propagate = lg.propagate
    lg.propagate = False                 # no duplicate record via the root logger when configure() hasn't run
    lg.addHandler(caplog.handler)
    yield caplog
    lg.removeHandler(caplog.handler)
    lg.propagate = propagate


@pytest.fixture(autouse=True)
def _clear_explain_cache():
    """app/graphs/common.py keeps finished explanations per process; tests reuse identical cards."""
    from app.graphs import common
    common._explain_cache.clear()
    yield
    common._explain_cache.clear()
