"""All SQL for the app. Synchronous psycopg pool; async graph nodes call these via asyncio.to_thread."""
import json
import random
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app import config
from app.core.explain import sample_card
from app.core.flavors import build_tag_to_category, load_tag_ko_extra, merge_tag_ko
from app.core.scoring import decaf_order_caffeine, decaf_twin_key, decaf_twins, is_milk_drink, needs_decaf_order
from app.models import ATTRS, Item, Neighbor, Profile
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


def brand_bean_columns() -> tuple[str, str]:
    """(house, decaf) brands columns for the running data variant: the open/competition variant reads only the
    licence-clean profiles (bean_open/decaf_bean_open; docs/adr/0012-official-brand-beans.md), never the full
    variant's, which were partly derived with models trained on coffeereview labels."""
    return ("bean_open", "decaf_bean_open") if config.DATA_VARIANT == "open" else ("bean", "decaf_bean")


class Repo:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 5):
        self.pool = ConnectionPool(url, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row},
                                   check=ConnectionPool.check_connection, max_idle=300, open=True)
        self._taxonomy: tuple[dict, dict] | None = None
        self._tag_base_rates: dict[str, float] | None = None
        self._needs_review_col: bool | None = None

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
            tag_ko = merge_tag_ko(tag_ko, load_tag_ko_extra())
            self._taxonomy = (tag_to_cat, tag_ko)
        return self._taxonomy

    def tag_base_rates(self) -> dict[str, float]:
        """Global base rate of each flavor tag: (#active coffees carrying it) / (#active coffees with >=1 tag).

        Feeds the lift gate in `predict_from_neighbors` so a tag that is common everywhere (e.g. "chocolate")
        needs a much higher share among a bean's neighbours before it counts as evidence."""
        if self._tag_base_rates is None:
            rows = self._all(
                "WITH counts AS ("
                "  SELECT t AS tag, count(*) AS n FROM coffees, unnest(flavor_tags) AS t WHERE active GROUP BY t"
                "), total AS (SELECT count(*) AS n FROM coffees WHERE active AND cardinality(flavor_tags) > 0)"
                " SELECT tag, n::float / NULLIF((SELECT n FROM total), 0) AS share FROM counts")
            self._tag_base_rates = {r["tag"].lower(): r["share"] for r in rows}
        return self._tag_base_rates

    # ---- brands & menus ---------------------------------------------------
    # Catalog queries (browse, search, recommend, neighbors, eval) skip retired rows (active = false);
    # direct lookups by id (get_coffee, get_menu_item, raw_menu) don't, so past tastings still resolve.
    def _served_menu(self, alias: str = "m") -> str:
        """SQL condition for menu rows the app may recommend: active and not waiting for a milk label
        (needs_review, docs/adr/0015-automated-refresh.md). A database migrated before that column existed has
        no unreviewed rows, so the condition is only added once the column is there (deploy-order safe)."""
        if self._needs_review_col is None:
            self._needs_review_col = self._one(
                "SELECT 1 AS ok FROM information_schema.columns WHERE table_schema = current_schema()"
                " AND table_name = 'menu_items' AND column_name = 'needs_review'") is not None
        return f"{alias}.active" + (f" AND NOT {alias}.needs_review" if self._needs_review_col else "")

    def list_brands(self) -> list[dict]:
        return self._all("SELECT b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, b.notes,"
                         f" EXISTS (SELECT 1 FROM menu_items m WHERE m.brand_id = b.id AND {self._served_menu()})"
                         " AS has_menu FROM brands b WHERE b.active ORDER BY b.name")

    def brand_items(self, brand_key: str, caffeine_rule: str) -> list[Item]:
        """The brand's drinks for this caffeine rule. A drink that would have to be ordered decaf is left out when the
        brand sells its own decaf SKU of it (이디야 "디카페인 카페 라떼", 폴바셋 "디카페인 카페라떼"): that SKU is
        already a candidate, with its real caffeine, so the guest gets it instead of "regular + swap" twice."""
        b = self._one("SELECT * FROM brands WHERE key = %s AND active", (brand_key,))
        if b is None:
            return []
        menus = self._all("SELECT m.id, m.name, m.is_decaf, m.decaf_option, m.caffeine_mg FROM menu_items m"
                          f" WHERE m.brand_id = %s AND {self._served_menu()} ORDER BY m.id", (b["id"],))
        if not menus:
            menus = [{"id": None, "name": n, "is_decaf": False, "decaf_option": b["decaf_available"],
                      "caffeine_mg": None} for n in SYNTHETIC_MENU]
        twins = decaf_twins(menus)
        items = [self._menu_item(b, m, caffeine_rule, twins) for m in menus]
        return [i for i in items if not (i.order_decaf and decaf_twin_key(i.name) in twins)]

    def _menu_item(self, b: dict, m: dict, caffeine_rule: str, twins: dict[str, list] | None = None) -> Item:
        order_decaf = needs_decaf_order(caffeine_rule, m["is_decaf"], m["decaf_option"], m["caffeine_mg"])
        caffeine, caffeine_note = m["caffeine_mg"], None
        if order_decaf:       # never show the regular drink's caffeine on a "order it decaf" card
            caffeine, caffeine_note = decaf_order_caffeine(m["name"], twins or {})
        house, decaf = brand_bean_columns()
        use_decaf = (m["is_decaf"] or order_decaf) and b.get(decaf)
        bean = (b[decaf] if use_decaf else b.get(house)) or {}
        note = bean.get("official_note")
        if use_decaf and not note and (b.get(house) or {}).get("official_note"):
            # no official word on the decaf bean (이디야·빽다방): the house bean's line, labelled as such
            note = f"{b[house]['official_note']} (일반 원두 기준 — 디카페인 원두는 공식 설명 없음)"
        key = f"menu:{m['id']}" if m["id"] is not None else f"{b['key']}:{m['name']}"
        return Item(key=key, name=m["name"], source="brand_bean", acidity=bean.get("acidity"), body=bean.get("body"),
                    sweetness=bean.get("sweetness"), tags=tuple(bean.get("flavor_tags", ())), is_decaf=m["is_decaf"],
                    decaf_option=m["decaf_option"], order_decaf=order_decaf, caffeine_mg=caffeine,
                    caffeine_mg_note=caffeine_note, is_milk=is_milk_drink(m["name"]), confidence="medium",
                    brand=b["name"], decaf_surcharge_krw=b["decaf_surcharge_krw"], menu_item_id=m["id"],
                    bean_note=note)

    def get_menu_item(self, menu_item_id: int, caffeine_rule: str) -> Item | None:
        m = self._one("SELECT id, brand_id, name, is_decaf, decaf_option, caffeine_mg FROM menu_items WHERE id = %s",
                      (menu_item_id,))
        if m is None:
            return None
        decaf_skus = self._all("SELECT name, is_decaf, caffeine_mg FROM menu_items"
                               " WHERE brand_id = %s AND active AND is_decaf", (m["brand_id"],))
        return self._menu_item(self._one("SELECT * FROM brands WHERE id = %s", (m["brand_id"],)), m, caffeine_rule,
                               decaf_twins(decaf_skus))

    def raw_menu(self, menu_item_id: int) -> dict | None:
        return self._one("SELECT m.name, m.is_decaf, m.decaf_option, m.caffeine_mg, b.decaf_available"
                         " FROM menu_items m JOIN brands b ON b.id = m.brand_id WHERE m.id = %s", (menu_item_id,))

    # ---- coffees ----------------------------------------------------------
    def get_coffee(self, coffee_id: int) -> Item | None:
        r = self._one(f"SELECT {COFFEE_COLS} FROM coffees WHERE id = %s", (coffee_id,))
        return _coffee_item(r) if r else None

    def count_coffees(self) -> int:
        return self._one("SELECT count(*) AS n FROM coffees WHERE active")["n"]

    def match_coffee(self, text: str) -> Item | None:
        t = " ".join((text or "").split()).lower()
        if not t:
            return None
        r = self._one(f"SELECT {COFFEE_COLS} FROM coffees WHERE active AND (lower(name) = %s"
                      " OR lower(coalesce(roaster, '') || ' ' || name) = %s) ORDER BY id LIMIT 1", (t, t))
        return _coffee_item(r) if r else None

    def search_coffees(self, q: str, limit: int = 8) -> list[dict]:
        q = (q or "").strip()
        if len(q) < 2 or not q.strip("%_\\"):
            return []
        like = f"%{_escape_like(q)}%"
        country = normalize_country(q)
        return self._all(
            "SELECT id, name, roaster, origin_country, is_decaf FROM coffees"
            " WHERE active AND (name ILIKE %(like)s OR roaster ILIKE %(like)s OR origin_country = %(country)s)"
            " ORDER BY (name ILIKE %(like)s) DESC, length(name), id LIMIT %(limit)s",
            {"like": like, "country": country, "limit": limit})

    def coffee_embedding(self, coffee_id: int) -> list[float] | None:
        r = self._one("SELECT embedding::text AS e FROM coffees WHERE id = %s", (coffee_id,))
        return json.loads(r["e"]) if r and r["e"] else None

    def neighbors(self, vec: list[float], k: int = 10, origin: str | None = None, process: str | None = None,
                  exclude_id: int | None = None, exclude_sources: tuple[str, ...] = ()) -> list[Neighbor]:
        v = to_vector_literal(vec)
        base = ((" AND id <> %(ex)s" if exclude_id is not None else "")
                + (" AND source <> ALL(%(xs)s)" if exclude_sources else ""))

        def run(extra: str) -> list[Neighbor]:
            with self.pool.connection() as conn, conn.transaction():
                conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                conn.execute("SET LOCAL hnsw.ef_search = 200")
                rows = conn.execute(
                    "SELECT id, name, acidity, body, sweetness, flavor_tags, 1 - (embedding <=> %(v)s::vector) AS sim"
                    f" FROM coffees WHERE active AND embedding IS NOT NULL{base}{extra}"
                    " ORDER BY embedding <=> %(v)s::vector, id LIMIT %(k)s",
                    {"v": v, "k": k, "ex": exclude_id, "o": origin, "p": process,
                     "xs": list(exclude_sources)}).fetchall()
            return [Neighbor(r["id"], r["name"], float(r["sim"]), r["acidity"], r["body"], r["sweetness"],
                             tuple(r["flavor_tags"] or ())) for r in sorted(rows, key=lambda r: (-r["sim"], r["id"]))]

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
            "SELECT id, name, acidity, body, sweetness, flavor_tags FROM coffees WHERE active"
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
        _, tag_ko = self.taxonomy()
        out = []
        for where in queries:
            r = self._one("SELECT id, name, flavor_tags FROM coffees WHERE active AND " + where +
                          " AND cardinality(flavor_tags) > 0 ORDER BY id LIMIT 1")
            if r:
                out.append(sample_card(r["id"], r["name"], r["flavor_tags"], tag_ko))
        return out

    # ---- evaluation helpers -----------------------------------------------
    def random_coffees_with_attrs(self, n: int, seed: int) -> list[Item]:
        rows = self._all(f"SELECT {COFFEE_COLS} FROM coffees WHERE active AND acidity IS NOT NULL AND body IS NOT NULL"
                         " AND sweetness IS NOT NULL ORDER BY id")
        return [_coffee_item(r) for r in random.Random(seed).sample(rows, min(n, len(rows)))]

    def random_coffee_ids_for_loo(self, n: int, seed: int, exclude_sources: tuple[str, ...] = (),
                                  sources: tuple[str, ...] = (), require: tuple[str, ...] = ("acidity",)) -> list[int]:
        """`sources` (when given) limits the targets to those sources; `exclude_sources` drops sources.
        `require` (attribute names from `app.models.ATTRS`) lists which attributes a target must have --
        default is acidity only.

        Eligibility requires acidity only by default, not every attribute (docs/adr/0010-body-heaviness.md):
        body is now sparse by design (CQI's "Body" was a quality score, not heaviness, so it's None -- see
        ADR 0010 -- and some coffeereview reviews simply don't mention weight). Requiring body too would have
        shrunk the eligible pool by exactly the coffees ADR 0010 correctly emptied, which also would have
        silently replaced the fixed acidity target draw with a different, non-comparable one.
        `app.eval.loo_accuracy` already scores each attribute only over the targets whose OWN truth value is
        present, so body/sweetness naturally get their own (smaller) n without needing a stricter target gate
        here.

        `require=("body",)` is how `app.eval.body_target_ids` draws a SEPARATE fixed target set for body
        specifically (coffeereview beans with a heaviness label) -- unrelated to this default."""
        require = tuple(dict.fromkeys(require))
        assert all(a in ATTRS for a in require), f"require must be a subset of {ATTRS}, got {require}"
        extra = ("".join(f" AND {a} IS NOT NULL" for a in require)
                 + (" AND source <> ALL(%(xs)s)" if exclude_sources else "")
                 + (" AND source = ANY(%(only)s)" if sources else ""))
        rows = self._all(f"SELECT id FROM coffees WHERE active AND embedding IS NOT NULL{extra} ORDER BY id",
                         {"xs": list(exclude_sources), "only": list(sources)})
        ids = [r["id"] for r in rows]
        return random.Random(seed).sample(ids, min(n, len(ids)))

    def coffee_sources(self, ids: list[int]) -> dict[int, str]:
        return {r["id"]: r["source"] for r in self._all("SELECT id, source FROM coffees WHERE id = ANY(%s)", (list(ids),))}

    def decaf_coffees(self, exclude_sources: tuple[str, ...] = ()) -> list[tuple[Item, str]]:
        """Active decaf beans with their source, for the decaf-drinker probe."""
        extra = " AND source <> ALL(%(xs)s)" if exclude_sources else ""
        rows = self._all(f"SELECT {COFFEE_COLS}, source FROM coffees WHERE active AND is_decaf{extra} ORDER BY id",
                         {"xs": list(exclude_sources)})
        return [(_coffee_item(r), r["source"]) for r in rows]

    def coverage_counts(self, exclude_sources: tuple[str, ...] = ()) -> dict:
        extra = " AND source <> ALL(%(xs)s)" if exclude_sources else ""
        row = self._one(
            "SELECT count(*) AS total,"
            " count(*) FILTER (WHERE embedding IS NOT NULL) AS with_embedding,"
            " count(*) FILTER (WHERE cardinality(flavor_tags) > 0) AS with_flavor_tags,"
            " count(*) FILTER (WHERE acidity IS NOT NULL) AS with_acidity,"
            " count(*) FILTER (WHERE is_decaf) AS decaf,"
            " count(*) FILTER (WHERE is_decaf AND cardinality(flavor_tags) > 0) AS decaf_with_flavor_tags"
            f" FROM coffees WHERE active{extra}", {"xs": list(exclude_sources)})
        menus = self._all("SELECT b.key, count(*) AS n FROM menu_items m JOIN brands b ON b.id = m.brand_id"
                          f" WHERE {self._served_menu()} GROUP BY b.key ORDER BY b.key")
        return {**dict(row), "menu_items_by_brand": {r["key"]: r["n"] for r in menus}}
