"""All SQL for the app. Synchronous psycopg pool; async graph nodes call these via asyncio.to_thread."""
import json
import random
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.flavors import build_tag_to_category
from app.core.scoring import LOW_CAFFEINE_MG, is_milk_drink
from app.models import Item, Neighbor, Profile
from pipeline.query import to_vector_literal
from pipeline.rules import normalize_country

MIN_FILTERED_NEIGHBORS = 5
SYNTHETIC_MENU = ("아메리카노", "카페라떼")      # for brands without scraped menus
COFFEE_COLS = "id, name, roaster, origin_country, process, is_decaf, acidity, body, sweetness, flavor_tags"


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _coffee_item(r: dict) -> Item:
    return Item(key=f"coffee:{r['id']}", name=r["name"], source="db", acidity=r["acidity"], body=r["body"],
                sweetness=r["sweetness"], tags=tuple(r["flavor_tags"] or ()), is_decaf=r["is_decaf"],
                confidence="high", brand=r["roaster"], coffee_id=r["id"], origin_country=r["origin_country"],
                process=r["process"])


class Repo:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 5):
        self.pool = ConnectionPool(url, min_size=min_size, max_size=max_size,
                                   kwargs={"row_factory": dict_row}, open=True)
        self._taxonomy: tuple[dict, dict] | None = None

    def close(self) -> None:
        self.pool.close()

    def _all(self, sql: str, params=None) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params=None) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    # ---- users & profiles -------------------------------------------------
    def create_user(self) -> str:
        return str(self._one("INSERT INTO users DEFAULT VALUES RETURNING id")["id"])

    def user_exists(self, uid: str | None) -> bool:
        try:
            UUID(str(uid))
        except ValueError:
            return False
        return self._one("SELECT 1 AS ok FROM users WHERE id = %s", (uid,)) is not None

    def set_nickname(self, uid: str, nickname: str | None) -> None:
        self._one("UPDATE users SET nickname = %s WHERE id = %s RETURNING id", (nickname, uid))

    def get_nickname(self, uid: str) -> str | None:
        row = self._one("SELECT nickname FROM users WHERE id = %s", (uid,))
        return row["nickname"] if row else None

    def get_profile(self, uid: str) -> Profile | None:
        r = self._one("SELECT * FROM taste_profiles WHERE user_id = %s", (uid,))
        if r is None:
            return None
        return Profile(caffeine_rule=r["caffeine_rule"], milk_ok=r["milk_ok"], acidity=r["acidity"], body=r["body"],
                       sweetness=r["sweetness"], flavor_weights=r["flavor_weights"], n_updates=r["n_updates"])

    def save_profile(self, uid: str, p: Profile, tasting_id: int | None = None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO taste_profiles (user_id, caffeine_rule, milk_ok, acidity, body, sweetness, flavor_weights,"
                " n_updates, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) ON CONFLICT (user_id) DO UPDATE SET"
                " caffeine_rule = EXCLUDED.caffeine_rule, milk_ok = EXCLUDED.milk_ok, acidity = EXCLUDED.acidity,"
                " body = EXCLUDED.body, sweetness = EXCLUDED.sweetness, flavor_weights = EXCLUDED.flavor_weights,"
                " n_updates = EXCLUDED.n_updates, updated_at = now()",
                (uid, p.caffeine_rule, p.milk_ok, p.acidity, p.body, p.sweetness, Jsonb(p.flavor_weights), p.n_updates))
            conn.execute("INSERT INTO profile_history (user_id, snapshot, tasting_id) VALUES (%s, %s, %s)",
                         (uid, Jsonb(p.to_dict()), tasting_id))

    def history(self, uid: str, limit: int = 20) -> list[dict]:
        return self._all("SELECT snapshot, tasting_id, created_at FROM profile_history WHERE user_id = %s"
                         " ORDER BY id DESC LIMIT %s", (uid, limit))

    def recent_tastings(self, uid: str, limit: int = 20) -> list[dict]:
        return self._all(
            "SELECT t.id, t.rating, t.note, t.created_at, coalesce(c.name, m.name, t.input_text) AS name"
            " FROM tastings t LEFT JOIN coffees c ON c.id = t.coffee_id LEFT JOIN menu_items m ON m.id = t.menu_item_id"
            " WHERE t.user_id = %s ORDER BY t.id DESC LIMIT %s", (uid, limit))

    def save_tasting(self, uid: str, *, coffee_id: int | None = None, menu_item_id: int | None = None,
                     input_text: str | None = None, predicted: dict | None = None, rating: int,
                     note: str | None = None, parsed_signals: dict | None = None) -> int:
        return self._one(
            "INSERT INTO tastings (user_id, coffee_id, menu_item_id, input_text, predicted, rating, note, parsed_signals)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (uid, coffee_id, menu_item_id, input_text, Jsonb(predicted) if predicted else None, rating, note,
             Jsonb(parsed_signals) if parsed_signals else None))["id"]

    # ---- taxonomy ---------------------------------------------------------
    def taxonomy(self) -> tuple[dict[str, str], dict[str, str]]:
        if self._taxonomy is None:
            rows = self._all("SELECT key, level, name_en, name_ko FROM flavor_taxonomy")
            tag_to_cat = build_tag_to_category((r["key"], r["level"], r["name_en"]) for r in rows)
            tag_ko = {r["name_en"].lower(): r["name_ko"] for r in rows if r["name_ko"]}
            self._taxonomy = (tag_to_cat, tag_ko)
        return self._taxonomy

    # ---- brands & menus ---------------------------------------------------
    def list_brands(self) -> list[dict]:
        return self._all("SELECT b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, b.notes,"
                         " EXISTS (SELECT 1 FROM menu_items m WHERE m.brand_id = b.id) AS has_menu"
                         " FROM brands b ORDER BY b.name")

    def brand_items(self, brand_key: str, want_decaf: bool) -> list[Item]:
        b = self._one("SELECT * FROM brands WHERE key = %s", (brand_key,))
        if b is None:
            return []
        menus = self._all("SELECT id, name, is_decaf, decaf_option, caffeine_mg FROM menu_items"
                          " WHERE brand_id = %s ORDER BY id", (b["id"],))
        if not menus:
            menus = [{"id": None, "name": n, "is_decaf": False, "decaf_option": b["decaf_available"],
                      "caffeine_mg": None} for n in SYNTHETIC_MENU]
        return [self._menu_item(b, m, want_decaf) for m in menus]

    def _menu_item(self, b: dict, m: dict, want_decaf: bool) -> Item:
        low_enough = m["caffeine_mg"] is not None and m["caffeine_mg"] <= LOW_CAFFEINE_MG
        order_decaf = bool(want_decaf and m["decaf_option"] and not m["is_decaf"] and not low_enough)
        bean = (b["decaf_bean"] if (m["is_decaf"] or order_decaf) and b["decaf_bean"] else b["bean"]) or {}
        key = f"menu:{m['id']}" if m["id"] is not None else f"{b['key']}:{m['name']}"
        return Item(key=key, name=m["name"], source="brand_bean", acidity=bean.get("acidity"), body=bean.get("body"),
                    sweetness=bean.get("sweetness"), tags=tuple(bean.get("flavor_tags", ())), is_decaf=m["is_decaf"],
                    decaf_option=m["decaf_option"], order_decaf=order_decaf, caffeine_mg=m["caffeine_mg"],
                    is_milk=is_milk_drink(m["name"]), confidence="medium", brand=b["name"],
                    decaf_surcharge_krw=b["decaf_surcharge_krw"], menu_item_id=m["id"])

    def get_menu_item(self, menu_item_id: int, want_decaf: bool) -> Item | None:
        row = self._one("SELECT b.key FROM menu_items m JOIN brands b ON b.id = m.brand_id WHERE m.id = %s",
                        (menu_item_id,))
        if row is None:
            return None
        return next((i for i in self.brand_items(row["key"], want_decaf) if i.menu_item_id == menu_item_id), None)

    def raw_menu(self, menu_item_id: int) -> dict | None:
        return self._one("SELECT m.name, m.is_decaf, m.decaf_option, m.caffeine_mg, b.decaf_available"
                         " FROM menu_items m JOIN brands b ON b.id = m.brand_id WHERE m.id = %s", (menu_item_id,))

    # ---- coffees ----------------------------------------------------------
    def get_coffee(self, coffee_id: int) -> Item | None:
        r = self._one(f"SELECT {COFFEE_COLS} FROM coffees WHERE id = %s", (coffee_id,))
        return _coffee_item(r) if r else None

    def match_coffee(self, text: str) -> Item | None:
        t = " ".join((text or "").split()).lower()
        if not t:
            return None
        r = self._one(f"SELECT {COFFEE_COLS} FROM coffees WHERE lower(name) = %s"
                      " OR lower(coalesce(roaster, '') || ' ' || name) = %s ORDER BY id LIMIT 1", (t, t))
        return _coffee_item(r) if r else None

    def search_coffees(self, q: str, limit: int = 8) -> list[dict]:
        q = (q or "").strip()
        if len(q) < 2 or not q.strip("%_\\"):
            return []
        like = f"%{_escape_like(q)}%"
        country = normalize_country(q)
        return self._all(
            "SELECT id, name, roaster, origin_country, is_decaf FROM coffees"
            " WHERE name ILIKE %(like)s OR roaster ILIKE %(like)s OR origin_country = %(country)s"
            " ORDER BY (name ILIKE %(like)s) DESC, length(name), id LIMIT %(limit)s",
            {"like": like, "country": country, "limit": limit})

    def coffee_embedding(self, coffee_id: int) -> list[float] | None:
        r = self._one("SELECT embedding::text AS e FROM coffees WHERE id = %s", (coffee_id,))
        return json.loads(r["e"]) if r and r["e"] else None

    def neighbors(self, vec: list[float], k: int = 10, origin: str | None = None, process: str | None = None,
                  exclude_id: int | None = None) -> list[Neighbor]:
        v = to_vector_literal(vec)
        base = " AND id <> %(ex)s" if exclude_id is not None else ""

        def run(extra: str) -> list[Neighbor]:
            with self.pool.connection() as conn, conn.transaction():
                conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                conn.execute("SET LOCAL hnsw.ef_search = 200")
                rows = conn.execute(
                    "SELECT id, name, acidity, body, sweetness, flavor_tags, 1 - (embedding <=> %(v)s::vector) AS sim"
                    f" FROM coffees WHERE embedding IS NOT NULL{base}{extra}"
                    " ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s",
                    {"v": v, "k": k, "ex": exclude_id, "o": origin, "p": process}).fetchall()
            return [Neighbor(r["id"], r["name"], float(r["sim"]), r["acidity"], r["body"], r["sweetness"],
                             tuple(r["flavor_tags"] or ())) for r in sorted(rows, key=lambda r: -r["sim"])]

        if origin or process:
            extra = (" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else "")
            hits = run(extra)
            if len(hits) >= MIN_FILTERED_NEIGHBORS:
                return hits
        return run("")

    def fallback_neighbors(self, origin: str | None, process: str | None, limit: int = 50) -> list[Neighbor]:
        if not origin and not process:
            return []
        rows = self._all(
            "SELECT id, name, acidity, body, sweetness, flavor_tags FROM coffees WHERE true"
            + (" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else "")
            + " ORDER BY id LIMIT %(limit)s", {"o": origin, "p": process, "limit": limit})
        return [Neighbor(r["id"], r["name"], 1.0, r["acidity"], r["body"], r["sweetness"],
                         tuple(r["flavor_tags"] or ())) for r in rows]

    def sample_coffees(self) -> list[dict]:
        queries = [
            "origin_country = 'Ethiopia' AND process = 'washed' AND acidity >= 4",      # bright washed
            "origin_country = 'Brazil' AND acidity <= 2",                               # chocolatey
            "process IN ('natural', 'anaerobic') AND flavor_tags && ARRAY['winey','fermented','alcohol fermented']",
        ]
        out = []
        for where in queries:
            r = self._one("SELECT id, name, flavor_summary, flavor_tags FROM coffees WHERE " + where +
                          " AND cardinality(flavor_tags) > 0 ORDER BY id LIMIT 1")
            if r:
                out.append({"coffee_id": r["id"], "name": r["name"], "summary": r["flavor_summary"],
                            "tags": r["flavor_tags"]})
        return out

    # ---- evaluation helpers -----------------------------------------------
    def random_coffees_with_attrs(self, n: int, seed: int) -> list[Item]:
        rows = self._all(f"SELECT {COFFEE_COLS} FROM coffees WHERE acidity IS NOT NULL AND body IS NOT NULL"
                         " AND sweetness IS NOT NULL ORDER BY id")
        return [_coffee_item(r) for r in random.Random(seed).sample(rows, min(n, len(rows)))]

    def random_coffee_ids_for_loo(self, n: int, seed: int) -> list[int]:
        rows = self._all("SELECT id FROM coffees WHERE embedding IS NOT NULL AND acidity IS NOT NULL"
                         " AND body IS NOT NULL ORDER BY id")
        ids = [r["id"] for r in rows]
        return random.Random(seed).sample(ids, min(n, len(ids)))
