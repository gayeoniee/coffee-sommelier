import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.eval import tag_names
from app.models import Profile
from app.repo import Repo
from pipeline import settings
from pipeline.query import to_vector_literal

pytestmark = pytest.mark.db


def vec(i):
    v = [0.0] * 1024
    v[i] = 1.0
    return v


@pytest.fixture
def repo(db_conn):
    from tests.conftest import TEST_URL
    c = db_conn
    c.execute("INSERT INTO flavor_taxonomy (key, level, name_en, name_ko) VALUES "
              "('sca:fruity', 1, 'fruity', '과일'), ('sca:fruity>citrus fruit', 2, 'citrus fruit', '시트러스')")
    c.execute("UPDATE flavor_taxonomy SET parent_id = (SELECT id FROM flavor_taxonomy WHERE key='sca:fruity') "
              "WHERE key = 'sca:fruity>citrus fruit'")
    for i, (name, origin, process, decaf, acid, tags) in enumerate([
            ("Ethiopia Yirgacheffe Washed", "Ethiopia", "washed", False, 5, ["citrus fruit"]),
            ("Decaf Ethiopia Sidamo", "Ethiopia", "natural", True, 4, ["citrus fruit"]),
            ("Brazil Cerrado", "Brazil", "natural", False, 1, ["chocolate"])]):
        c.execute("INSERT INTO coffees (key, name, roaster, origin_country, process, is_decaf, acidity, body, "
                  "sweetness, flavor_tags, flavor_summary, embedding, source, collected_at) "
                  "VALUES (%s,%s,'R',%s,%s,%s,%s,3,3,%s,%s,%s::vector,'t','2026-09-26')",
                  (f"c{i}", name, origin, process, decaf, acid, tags, f"summary {i}", to_vector_literal(vec(i))))
    bean = Jsonb({"acidity": 2, "body": 4, "sweetness": 2, "flavor_tags": ["chocolate"],
                  "official_note": "공식: 하우스 블렌드 — 강한 바디감"})
    dbean = Jsonb({"acidity": 2, "body": 3, "sweetness": 3, "flavor_tags": ["caramelized"],
                   "official_note": "공식: 디카페인 — 캐러멜 향"})
    plain_bean = Jsonb({"acidity": 2, "body": 4, "sweetness": 2, "flavor_tags": ["chocolate"]})
    bean_open = Jsonb({"acidity": 3, "body": 4, "sweetness": 4.5, "flavor_tags": ["caramelized"],
                       "official_note": "공식: 하우스 블렌드 — 강한 바디감"})
    dbean_open = Jsonb({"acidity": 2.5, "body": 3, "sweetness": 3, "flavor_tags": ["nutty"],
                        "official_note": "공식: 디카페인 — 캐러멜 향"})
    c.execute("INSERT INTO brands (key, name, decaf_available, decaf_surcharge_krw, verified_at, bean, decaf_bean, "
              "bean_open, decaf_bean_open) VALUES "
              "('brand:sb', '스타벅스', true, 300, '2026-09-24', %s, %s, %s, %s), "
              "('brand:tw', '투썸', true, 200, '2026-09-24', %s, %s, %s, %s)",
              (bean, dbean, bean_open, dbean_open, plain_bean, plain_bean, plain_bean, plain_bean))
    c.execute("INSERT INTO menu_items (key, brand_id, name, is_decaf, decaf_option, caffeine_mg, collected_at) VALUES "
              "('m1', (SELECT id FROM brands WHERE key='brand:sb'), '아메리카노', false, true, 150, '2026-09-24'), "
              "('m2', (SELECT id FROM brands WHERE key='brand:sb'), '카페 라떼', false, true, 75, '2026-09-24')")
    c.commit()
    r = Repo(TEST_URL)
    yield r
    r.close()


def test_users_profiles_history(repo):
    uid = repo.create_user()
    assert repo.user_exists(uid) and not repo.user_exists("not-a-uuid")
    assert not repo.user_exists("00000000-0000-0000-0000-000000000000")
    assert repo.get_profile(uid) is None
    repo.save_profile(uid, Profile(caffeine_rule="decaf_only", acidity=4.5))
    repo.save_profile(uid, Profile(caffeine_rule="decaf_only", acidity=4.0, n_updates=1))
    assert repo.get_profile(uid).acidity == 4.0
    assert [h["snapshot"]["acidity"] for h in repo.history(uid)] == [4.0, 4.5]
    repo.set_nickname(uid, "가연")
    assert repo.get_nickname(uid) == "가연"


def test_taxonomy_maps(repo):
    tag_to_cat, tag_ko = repo.taxonomy()
    assert tag_to_cat == {"citrus fruit": "fruity"}
    assert tag_ko["citrus fruit"] == "시트러스"


def test_tag_base_rates(repo):
    # fixture: 3 active coffees with a tag - 2 carry "citrus fruit", 1 carries "chocolate"
    rates = repo.tag_base_rates()
    assert rates == pytest.approx({"citrus fruit": 2 / 3, "chocolate": 1 / 3})


def test_taxonomy_tag_ko_is_topped_up_by_the_extra_file(repo):
    # fixture's flavor_taxonomy only has "fruity"/"citrus fruit" - "milk chocolate" has no taxonomy row at all,
    # so it can only come from config/tag_ko_extra.yaml (Fix B).
    _, tag_ko = repo.taxonomy()
    assert tag_ko["milk chocolate"] == "밀크 초콜릿"
    assert tag_ko["citrus fruit"] == "시트러스"          # taxonomy names are still there alongside the extra ones


def test_brand_items_carry_the_official_bean_note_of_the_bean_actually_used(repo):
    """ADR 0012: the card evidence line follows the bean behind the drink -- decaf order → decaf bean's note."""
    assert {i.name: i for i in repo.brand_items("brand:sb", "any")}["아메리카노"].bean_note == "공식: 하우스 블렌드 — 강한 바디감"
    assert {i.name: i for i in repo.brand_items("brand:sb", "decaf_only")}["아메리카노"].bean_note == "공식: 디카페인 — 캐러멜 향"


def test_brand_items_use_the_open_profiles_only_under_the_open_variant(repo, monkeypatch):
    """ADR 0012 오픈판: DATA_VARIANT=open reads bean_open/decaf_bean_open (licence-clean), never bean/decaf_bean."""
    from app import config

    def attrs(caffeine_rule):
        am = {i.name: i for i in repo.brand_items("brand:sb", caffeine_rule)}["아메리카노"]
        return am.acidity, am.body, am.sweetness, am.tags

    monkeypatch.setattr(config, "DATA_VARIANT", "full")
    assert attrs("any") == (2, 4, 2, ("chocolate",))
    assert attrs("decaf_only") == (2, 3, 3, ("caramelized",))
    monkeypatch.setattr(config, "DATA_VARIANT", "open")
    assert attrs("any") == (3, 4, 4.5, ("caramelized",))
    assert attrs("decaf_only") == (2.5, 3, 3, ("nutty",))
    menu_id = {i.name: i for i in repo.brand_items("brand:sb", "any")}["아메리카노"].menu_item_id
    assert repo.get_menu_item(menu_id, "decaf_only").tags == ("nutty",)


def test_brand_items_skip_menu_items_waiting_for_a_milk_label(repo, db_conn):
    """ADR 0015: needs_review items (new names with no hand milk label) are never recommended, nor counted."""
    db_conn.execute("INSERT INTO menu_items (key, brand_id, name, collected_at, needs_review) VALUES "
                    "('m9', (SELECT id FROM brands WHERE key='brand:sb'), '신메뉴 크림 콜드브루', '2026-09-28', true)")
    db_conn.commit()
    assert "신메뉴 크림 콜드브루" not in {i.name for i in repo.brand_items("brand:sb", "any")}
    assert repo.coverage_counts()["menu_items_by_brand"] == {"brand:sb": 2}
    db_conn.execute("UPDATE menu_items SET needs_review = false WHERE key = 'm9'")
    db_conn.commit()
    assert "신메뉴 크림 콜드브루" in {i.name for i in repo.brand_items("brand:sb", "any")}


def test_brand_items_work_on_a_database_without_the_needs_review_column(repo, db_conn):
    """Deploy-order safety: the app may start against a DB migrated before the column existed."""
    db_conn.execute("ALTER TABLE menu_items DROP COLUMN needs_review")
    db_conn.commit()
    try:
        fresh = Repo(repo.pool.conninfo)
        assert {i.name for i in fresh.brand_items("brand:sb", "any")} == {"아메리카노", "카페 라떼"}
        fresh.close()
    finally:
        db_conn.execute("ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false")
        db_conn.commit()


def test_brand_items_decaf_option_and_synthetic_menu(repo):
    items = {i.name: i for i in repo.brand_items("brand:sb", "decaf_only")}
    am = items["아메리카노"]
    assert (am.order_decaf, am.decaf_surcharge_krw, am.brand, am.source) == (True, 300, "스타벅스", "brand_bean")
    assert am.tags == ("caramelized",)                       # decaf bean attributes used
    assert items["카페 라떼"].is_milk is True
    plain = {i.name: i for i in repo.brand_items("brand:sb", "any")}
    assert plain["아메리카노"].order_decaf is False and plain["아메리카노"].tags == ("chocolate",)
    synthetic = repo.brand_items("brand:tw", "decaf_only")   # brand without menu rows
    assert [i.name for i in synthetic] == ["아메리카노", "카페라떼"]
    assert all(i.decaf_option and i.menu_item_id is None and i.order_decaf for i in synthetic)
    assert all(i.bean_note is None for i in synthetic)         # no official description → no note
    assert repo.brand_items("brand:none", "any") == []
    assert repo.get_menu_item(am.menu_item_id, "decaf_only").order_decaf is True


def test_brand_items_and_scoring_work_without_a_bean_profile(repo, db_conn):
    """ADR 0023 Phase 2: brands sourced from the MFDS food DB have no official bean description at all
    (bean/bean_open null, data/curated/brands.yaml) -- cards must still carry caffeine/decaf/milk and
    score as neutral (0.5), never crash, when there is no taste profile to match against."""
    from app.core.scoring import attr_fit, score_item

    db_conn.execute("INSERT INTO brands (key, name, decaf_available, verified_at, bean, decaf_bean, bean_open,"
                    " decaf_bean_open) VALUES ('brand:nb', '노빈', false, '2026-09-30', NULL, NULL, NULL, NULL)")
    db_conn.execute(
        "INSERT INTO menu_items (key, brand_id, name, is_decaf, decaf_option, caffeine_mg, source, collected_at)"
        " VALUES ('nb1', (SELECT id FROM brands WHERE key='brand:nb'), '카페 라떼(HOT)', false, false, 150,"
        " 'mfds_food', '2026-09-30')")
    db_conn.commit()
    tag_to_cat, _ = repo.taxonomy()
    [item] = repo.brand_items("brand:nb", "any")
    assert (item.name, item.caffeine_mg, item.is_decaf, item.is_milk) == ("카페 라떼(HOT)", 150, False, True)
    assert (item.acidity, item.body, item.sweetness, item.tags) == (None, None, None, ())
    assert item.confidence == "low"
    assert attr_fit(Profile(caffeine_rule="any", acidity=4, body=4, sweetness=4), item) is None
    assert score_item(Profile(caffeine_rule="any", acidity=4, body=4, sweetness=4), item, tag_to_cat) == 0.5


def _decaf_sku_brand(db_conn):
    """이디야-like brand: official house note only, decaf SKUs of some drinks, plus a drink with no decaf SKU."""
    house = Jsonb({"acidity": 2, "body": 4, "sweetness": 3, "flavor_tags": ["smoky"],
                   "official_note": "공식: 블렌드 원두 — 스모키"})
    dbean = Jsonb({"acidity": 2, "body": 3, "sweetness": 3, "flavor_tags": ["caramelized"], "official_note": None})
    db_conn.execute("INSERT INTO brands (key, name, decaf_available, verified_at, bean, decaf_bean, bean_open,"
                    " decaf_bean_open) VALUES ('brand:ed', '이디야', true, '2026-09-28', %s, %s, %s, %s)",
                    (house, dbean, house, dbean))
    for key, name, is_decaf, opt, mg in [("e1", "카페 라떼", False, True, 202), ("e2", "디카페인 카페 라떼", True, False, 7),
                                         ("e3", "달달커피", False, True, 162), ("e4", "디카페인 카페 모카", True, False, 11),
                                         ("e5", "제로슈가 달달커피", False, True, 47)]:
        db_conn.execute("INSERT INTO menu_items (key, brand_id, name, is_decaf, decaf_option, caffeine_mg, collected_at)"
                        " VALUES (%s, (SELECT id FROM brands WHERE key='brand:ed'), %s, %s, %s, %s, '2026-09-28')",
                        (key, name, is_decaf, opt, mg))
    db_conn.commit()


def test_decaf_sku_replaces_regular_plus_swap_and_caffeine_is_never_the_regular_mg(repo, db_conn):
    _decaf_sku_brand(db_conn)
    decaf = {i.name: i for i in repo.brand_items("brand:ed", "decaf_only")}
    assert "카페 라떼" not in decaf                           # its decaf SKU is the candidate instead: no duplicate
    assert (decaf["디카페인 카페 라떼"].caffeine_mg, decaf["디카페인 카페 라떼"].caffeine_mg_note) == (7, None)
    sweet = decaf["달달커피"]                                   # no decaf SKU of its own -> swap, brand median shown
    assert (sweet.order_decaf, sweet.caffeine_mg, sweet.caffeine_mg_note) == (True, 9.0, "디카페인 주문 시 추정")
    low = {i.name: i for i in repo.brand_items("brand:ed", "low")}
    assert "카페 라떼" not in low and low["달달커피"].caffeine_mg == 9.0
    assert low["제로슈가 달달커피"].order_decaf is False and low["제로슈가 달달커피"].caffeine_mg == 47
    plain = {i.name: i for i in repo.brand_items("brand:ed", "any")}
    assert plain["카페 라떼"].caffeine_mg == 202 and plain["카페 라떼"].caffeine_mg_note is None
    latte_id = plain["카페 라떼"].menu_item_id                  # logging the regular drink as ordered decaf
    logged = repo.get_menu_item(latte_id, "decaf_only")
    assert (logged.order_decaf, logged.caffeine_mg, logged.caffeine_mg_note) == (True, 7.0, "디카페인 주문 시 추정")
    sb = {i.name: i for i in repo.brand_items("brand:sb", "decaf_only")}["아메리카노"]   # brand without decaf SKUs
    assert (sb.caffeine_mg, sb.caffeine_mg_note) == (None, "디카페인 주문 시 카페인 ↓")


def test_decaf_card_falls_back_to_the_labelled_house_note_when_the_decaf_bean_has_none(repo, db_conn, monkeypatch):
    """이디야: official house-bean note, none for the decaf bean -> decaf cards still get an evidence line."""
    from app import config
    _decaf_sku_brand(db_conn)
    for variant in ("full", "open"):
        monkeypatch.setattr(config, "DATA_VARIANT", variant)
        items = {i.name: i for i in repo.brand_items("brand:ed", "decaf_only")}
        for name in ("디카페인 카페 라떼", "달달커피"):
            assert items[name].bean_note == "공식: 블렌드 원두 — 스모키 (일반 원두 기준 — 디카페인 원두는 공식 설명 없음)"
        assert {i.name: i for i in repo.brand_items("brand:ed", "any")}["카페 라떼"].bean_note == "공식: 블렌드 원두 — 스모키"


def test_order_decaf_depends_on_caffeine_rule(repo):
    def latte(rule):
        return {i.name: i for i in repo.brand_items("brand:sb", rule)}["카페 라떼"]

    assert latte("decaf_only").order_decaf is True           # 75mg is low, but decaf_only still needs decaf
    assert latte("decaf_only").tags == ("caramelized",)
    assert latte("low").order_decaf is False                 # 75mg is already low enough
    assert latte("low").tags == ("chocolate",)
    assert latte("any").order_decaf is False
    americano = {i.name: i for i in repo.brand_items("brand:sb", "low")}["아메리카노"]
    assert americano.order_decaf is True                     # 150mg > 100mg -> order decaf
    mid = americano.menu_item_id
    assert repo.get_menu_item(mid, "low").order_decaf is True and repo.get_menu_item(mid, "any").order_decaf is False


def test_coffee_lookup_search_and_match(repo):
    hit = repo.search_coffees("ethiopia")
    assert [h["name"] for h in hit] == ["Decaf Ethiopia Sidamo", "Ethiopia Yirgacheffe Washed"]   # shorter name first
    assert [h["name"] for h in repo.search_coffees("브라질")] == ["Brazil Cerrado"]      # Korean origin
    assert repo.search_coffees("%") == [] and repo.search_coffees("a") == []           # wildcard/too short
    it = repo.match_coffee("brazil cerrado")
    assert it.source == "db" and it.coffee_id is not None and it.acidity == 1
    assert repo.match_coffee("unknown bean") is None
    assert repo.get_coffee(it.coffee_id).name == "Brazil Cerrado"


def test_neighbors_prefer_same_origin_and_exclude(repo):
    target = repo.match_coffee("Ethiopia Yirgacheffe Washed").coffee_id
    near = repo.neighbors(vec(0), k=2, exclude_id=target)
    assert target not in [n.coffee_id for n in near] and len(near) == 2
    assert repo.coffee_embedding(target)[0] == 1.0
    fb = repo.fallback_neighbors("Ethiopia", None)
    assert {n.name for n in fb} == {"Ethiopia Yirgacheffe Washed", "Decaf Ethiopia Sidamo"}
    assert repo.fallback_neighbors(None, None) == []


def test_neighbors_and_loo_ids_exclude_sources(repo):
    with repo.pool.connection() as conn:
        restricted_id = conn.execute(
            "INSERT INTO coffees (key, name, roaster, origin_country, process, is_decaf, acidity, body, "
            "sweetness, flavor_tags, flavor_summary, embedding, source, collected_at) "
            "VALUES ('c3','Restricted Bean','R','Ethiopia','washed',false,5,3,3,%s,'restricted',%s::vector,"
            "'coffeereview_kaggle','2026-09-26') RETURNING id",
            (["citrus fruit"], to_vector_literal(vec(0)))).fetchone()["id"]
    target = repo.match_coffee("Ethiopia Yirgacheffe Washed").coffee_id

    near = repo.neighbors(vec(0), k=10, exclude_id=target)
    assert restricted_id in [n.coffee_id for n in near]
    near_open = repo.neighbors(vec(0), k=10, exclude_id=target, exclude_sources=("coffeereview_kaggle",))
    assert restricted_id not in [n.coffee_id for n in near_open]

    ids = repo.random_coffee_ids_for_loo(10, seed=1)
    assert restricted_id in ids
    ids_open = repo.random_coffee_ids_for_loo(10, seed=1, exclude_sources=("coffeereview_kaggle",))
    assert restricted_id not in ids_open


def test_coverage_counts_excludes_sources(repo):
    with repo.pool.connection() as conn:
        conn.execute(
            "INSERT INTO coffees (key, name, roaster, origin_country, process, is_decaf, acidity, body, "
            "sweetness, flavor_tags, flavor_summary, embedding, source, collected_at) "
            "VALUES ('c3','Restricted Bean','R','Ethiopia','washed',false,5,3,3,%s,'restricted',%s::vector,"
            "'coffeereview_kaggle','2026-09-26')",
            (["citrus fruit"], to_vector_literal(vec(0))))
    full = repo.coverage_counts()
    assert (full["total"], full["with_embedding"], full["with_flavor_tags"], full["with_acidity"], full["decaf"],
            full["decaf_with_flavor_tags"]) == (4, 4, 4, 4, 1, 1)
    open_ = repo.coverage_counts(exclude_sources=("coffeereview_kaggle",))
    assert (open_["total"], open_["with_flavor_tags"], open_["decaf"]) == (3, 3, 1)


def test_save_tasting_and_recent(repo):
    uid = repo.create_user()
    cid = repo.match_coffee("Brazil Cerrado").coffee_id
    tid = repo.save_tasting(uid, coffee_id=cid, rating=4, note="고소해요", parsed_signals={"body": "higher"})
    repo.save_tasting(uid, input_text="동네 블렌드", predicted={"acidity": 3}, rating=2)
    rows = repo.recent_tastings(uid)
    assert [r["name"] for r in rows] == ["동네 블렌드", "Brazil Cerrado"] and rows[1]["id"] == tid


def test_sample_coffees_have_korean_tags_and_no_review_text(repo):
    samples = repo.sample_coffees()
    assert [s["name"] for s in samples] == ["Ethiopia Yirgacheffe Washed", "Brazil Cerrado"]
    assert all(set(s) == {"coffee_id", "name", "tags", "tags_ko", "description"} for s in samples)
    eth, bra = samples
    assert eth["tags_ko"] == ["시트러스"] and eth["description"] == "시트러스 향이 나는 원두"
    assert bra["tags_ko"] == ["chocolate"]                   # no taxonomy name -> the tag itself
    assert "summary" not in str(samples)


def test_retired_catalog_rows_are_hidden_but_still_resolvable(repo):
    brazil = repo.match_coffee("Brazil Cerrado").coffee_id
    latte = {i.name: i for i in repo.brand_items("brand:sb", "any")}["카페 라떼"].menu_item_id
    with repo.pool.connection() as conn:                 # what pipeline.load does to a protected vanished row
        conn.execute("UPDATE coffees SET active = false WHERE id = %s", (brazil,))
        conn.execute("UPDATE menu_items SET active = false WHERE id = %s", (latte,))
        conn.execute("UPDATE brands SET active = false WHERE key = 'brand:tw'")
    assert repo.search_coffees("brazil") == [] and repo.match_coffee("brazil cerrado") is None
    assert brazil not in [n.coffee_id for n in repo.neighbors(vec(2), k=10)]
    assert repo.fallback_neighbors("Brazil", None) == []
    assert brazil not in repo.random_coffee_ids_for_loo(10, seed=1)
    assert brazil not in [i.coffee_id for i in repo.random_coffees_with_attrs(10, seed=1)]
    assert repo.coverage_counts()["total"] == 2
    assert "Brazil Cerrado" not in [s["name"] for s in repo.sample_coffees()]
    assert [b["key"] for b in repo.list_brands()] == ["brand:sb"]
    assert [i.name for i in repo.brand_items("brand:sb", "any")] == ["아메리카노"]
    assert repo.brand_items("brand:tw", "any") == []
    # direct lookups (tasting history, logging a past recommendation) still work
    assert repo.get_coffee(brazil).name == "Brazil Cerrado"
    assert repo.get_menu_item(latte, "decaf_only").name == "카페 라떼"
    assert repo.get_menu_item(latte, "decaf_only").order_decaf is True
    assert repo.raw_menu(latte)["name"] == "카페 라떼"


def test_pool_replaces_connections_killed_by_the_server(repo, db_conn):
    assert repo.pool.max_idle == 300
    with repo.pool.connection() as conn:
        pid = conn.info.backend_pid
    db_conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
    db_conn.commit()
    assert repo.user_exists(repo.create_user())           # a dead pooled connection is checked and replaced


def _insert_two_identical_embeddings(repo, v, ids=(900002, 900001)):
    """Two coffees with the same embedding vector, inserted with explicit ids in the given order — by default
    the higher id first, so physical (insertion) order and id order disagree."""
    with repo.pool.connection() as conn:
        for i in ids:
            conn.execute(
                "INSERT INTO coffees (id, key, name, roaster, is_decaf, acidity, body, sweetness, flavor_tags,"
                " embedding, source, collected_at) VALUES (%s,%s,'Tie Bean','R',false,3,3,3,%s,%s::vector,'t',"
                "'2026-09-26')", (i, f"tie{i}", ["chocolate"], to_vector_literal(v)))
    return sorted(ids)


def test_neighbors_break_ties_by_id(repo):
    # identical embeddings inserted higher-id first: without the id tie-break they come back in physical order
    lo, hi = _insert_two_identical_embeddings(repo, vec(9))
    a = [n.coffee_id for n in repo.neighbors(vec(9), k=5)]
    b = [n.coffee_id for n in repo.neighbors(vec(9), k=5)]
    assert a == b
    assert a.index(lo) < a.index(hi)


def test_loo_target_filter_sources_and_decaf_beans(repo):
    with repo.pool.connection() as conn:
        rid = conn.execute(
            "INSERT INTO coffees (key, name, roaster, is_decaf, acidity, body, flavor_tags, embedding, source,"
            " collected_at) VALUES ('k1','디카페인 콜롬비아','프릳츠',true,4,2,%s,%s::vector,'roasters_kr','2026-09-26')"
            " RETURNING id", (["chocolate"], to_vector_literal(vec(5)))).fetchone()["id"]
    assert repo.random_coffee_ids_for_loo(10, seed=1, sources=("roasters_kr",)) == [rid]
    assert rid not in repo.random_coffee_ids_for_loo(10, seed=1, sources=("t",))
    assert repo.coffee_sources([rid])[rid] == "roasters_kr"
    assert sorted(src for _, src in repo.decaf_coffees()) == ["roasters_kr", "t"]
    only_open = repo.decaf_coffees(exclude_sources=("roasters_kr",))
    assert [(i.name, src) for i, src in only_open] == [("Decaf Ethiopia Sidamo", "t")]


def test_loo_target_gate_requires_acidity_only_not_body(repo):
    """docs/adr/0010-body-heaviness.md: CQI's body is None by design (it was a quality score, not a heaviness
    fact -- see the ADR), and some coffeereview reviews just don't mention weight either. A target eligible on
    acidity alone must still be drawn (and scored for acidity); body/sweetness are then scored only over
    whichever of those targets happen to have their own truth value present."""
    with repo.pool.connection() as conn:
        rid = conn.execute(
            "INSERT INTO coffees (key, name, roaster, is_decaf, acidity, sweetness, flavor_tags, embedding,"
            " source, collected_at) VALUES ('cqi1','CQI Sample','R',false,4,3,%s,%s::vector,'cqi_like',"
            "'2026-09-26') RETURNING id", (["chocolate"], to_vector_literal(vec(3)))).fetchone()["id"]
    assert repo.get_coffee(rid).body is None                      # body genuinely absent, not just unset here

    # the gate itself: this id is eligible for LOO despite body being null
    assert repo.random_coffee_ids_for_loo(10, seed=1, sources=("cqi_like",)) == [rid]

    # end to end through loo_accuracy: acidity/sweetness score it, body skips it (n stays 0)
    from app.eval import loo_accuracy
    result = loo_accuracy(repo, n=10, seed=1, target_sources=("cqi_like",))
    assert result["targets"] == 1
    assert result["acidity"]["n"] == 1
    assert result["sweetness"]["n"] == 1
    assert result["body"]["n"] == 0


def test_random_coffee_ids_require_lets_body_targets_skip_the_acidity_gate(repo):
    """docs/adr/0010-body-heaviness.md's second, body-only target set (app.eval.BODY_TARGET_SOURCES /
    body_target_ids) needs the OPPOSITE gate from the default: body present, acidity irrelevant. `require`
    is how that is expressed without touching the default acidity-only gate every other caller relies on."""
    with repo.pool.connection() as conn:
        has_body = conn.execute(
            "INSERT INTO coffees (key, name, roaster, is_decaf, body, flavor_tags, embedding, source,"
            " collected_at) VALUES ('cr1','Has Body','R',false,4,%s,%s::vector,'coffeereview_kaggle',"
            "'2026-09-26') RETURNING id", (["chocolate"], to_vector_literal(vec(6)))).fetchone()["id"]
        no_body = conn.execute(
            "INSERT INTO coffees (key, name, roaster, is_decaf, acidity, flavor_tags, embedding, source,"
            " collected_at) VALUES ('cr2','No Body','R',false,4,%s,%s::vector,'coffeereview_kaggle',"
            "'2026-09-26') RETURNING id", (["chocolate"], to_vector_literal(vec(7)))).fetchone()["id"]
    assert repo.get_coffee(has_body).acidity is None                # acidity genuinely absent here
    assert repo.get_coffee(no_body).body is None                    # body genuinely absent here

    # default gate (acidity only): picks the acidity bean, not the body-only one
    assert repo.random_coffee_ids_for_loo(10, seed=1, sources=("coffeereview_kaggle",)) == [no_body]
    # require=("body",): picks the body bean instead, despite its acidity being null
    assert repo.random_coffee_ids_for_loo(10, seed=1, sources=("coffeereview_kaggle",),
                                          require=("body",)) == [has_body]

    with pytest.raises(AssertionError):
        repo.random_coffee_ids_for_loo(10, seed=1, require=("not_a_real_attr",))


def test_compare3_scores_body_on_its_own_fixed_target_set(repo):
    """compare3's CQI-fixed acidity targets never have a body label (ADR 0010) -- `loo.body.n` stays 0 for
    every variant regardless. The separate `body_loo` block, scored against coffeereview_kaggle beans that DO
    have a body label, is where a real body accuracy shows up (docs/adr/0010-body-heaviness.md follow-up)."""
    with repo.pool.connection() as conn:
        conn.execute(
            "INSERT INTO coffees (key, name, roaster, is_decaf, body, flavor_tags, embedding, source,"
            " collected_at) VALUES ('cr1','Body Target','R',false,4,%s,%s::vector,'coffeereview_kaggle',"
            "'2026-09-26')", (["chocolate"], to_vector_literal(vec(8))))
    from app.eval import compare3
    result = compare3(repo, n=10, seed=1)
    assert result["body_targets"]["sources"] == ["coffeereview_kaggle"]
    assert result["body_targets"]["identical_across_variants"] is True
    for name in ("full", "open", "open_plus"):
        variant = result["variants"][name]
        assert variant["loo"]["body"]["n"] == 0            # unchanged: the CQI-fixed set still has no body
        body = variant["body_loo"]["body"]
        assert body["n"] == 1
        assert body["within1"] == 1.0
        assert body["mae"] == 1.0                          # neighbour avg predicts 3.0 vs truth 4


def test_dev_db_every_active_flavor_tag_has_a_korean_name():
    """Fix B: no English tag (e.g. "milk chocolate") should reach the UI untranslated. Checks the real dev DB
    (not the throwaway coffee_test fixture) since that's where the actual catalog's tags live."""
    try:
        with psycopg.connect(settings.DATABASE_URL, autocommit=True, connect_timeout=3) as admin:
            loaded = admin.execute("SELECT to_regclass('public.flavor_taxonomy') IS NOT NULL"
                                   " AND (SELECT count(*) FROM coffees WHERE active) >= 100").fetchone()[0]   # a real catalog, not fixtures
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        pytest.skip("dev DB not reachable (docker compose up -d db)")
    if not loaded:
        pytest.skip("dev DB has no loaded catalog (CI / fixture databases)")
    repo = Repo(settings.DATABASE_URL)
    try:
        result = tag_names(repo)
    finally:
        repo.close()
    assert result["missing_ko"] == []


def test_neighbors_tagged_only_skips_untagged_beans(repo):
    with repo.pool.connection() as conn:
        untagged = conn.execute(
            "INSERT INTO coffees (key, name, roaster, origin_country, process, is_decaf, acidity, body, sweetness, "
            "flavor_tags, embedding, source, collected_at) VALUES ('cq','CQI Row','R',NULL,NULL,false,3,3,3,'{}',"
            "%s::vector,'cqi','2026-09-26') RETURNING id", (to_vector_literal(vec(0)),)).fetchone()["id"]
    assert untagged in [n.coffee_id for n in repo.neighbors(vec(0), k=10)]
    near = repo.neighbors(vec(0), k=10, tagged_only=True)
    assert untagged not in [n.coffee_id for n in near] and near and all(n.tags for n in near)
