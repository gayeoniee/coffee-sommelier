import psycopg

from pipeline import settings

SCHEMA_PATH = settings.ROOT / "db" / "schema.sql"
TABLES = ["menu_items", "brands", "reviews", "coffees", "flavor_taxonomy", "enrich_log"]


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or settings.DATABASE_URL)


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def reset_tables(conn: psycopg.Connection) -> None:
    conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()
