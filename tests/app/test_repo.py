import pytest
from psycopg.types.json import Jsonb

from app.models import Profile
from app.repo import Repo
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
    bean = Jsonb({"acidity": 2, "body": 4, "sweetness": 2, "flavor_tags": ["chocolate"]})
    dbean = Jsonb({"acidity": 2, "body": 3, "sweetness": 3, "flavor_tags": ["caramelized"]})
    c.execute("INSERT INTO brands (key, name, decaf_available, decaf_surcharge_krw, verified_at, bean, decaf_bean) VALUES "
              "('brand:sb', '스타벅스', true, 300, '2026-09-24', %s, %s), "
              "('brand:tw', '투썸', true, 200, '2026-09-24', %s, %s)", (bean, dbean, bean, dbean))
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
    assert repo.brand_items("brand:none", "any") == []
    assert repo.get_menu_item(am.menu_item_id, "decaf_only").order_decaf is True


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


def _insert_two_identical_embeddings(repo, v):
    """Two coffees with the same embedding vector; returns their ids sorted ascending."""
    with repo.pool.connection() as conn:
        ids = [conn.execute(
            "INSERT INTO coffees (key, name, roaster, is_decaf, acidity, body, sweetness, flavor_tags,"
            " embedding, source, collected_at) VALUES (%s,'Tie Bean','R',false,3,3,3,%s,%s::vector,'t',"
            "'2026-09-26') RETURNING id",
            (f"tie{i}", ["chocolate"], to_vector_literal(v))).fetchone()["id"] for i in range(2)]
    return sorted(ids)


def test_neighbors_break_ties_by_id(repo):
    # two coffees with identical embeddings must come back in id order, run after run
    ids = _insert_two_identical_embeddings(repo, vec(9))
    a = [n.coffee_id for n in repo.neighbors(vec(9), k=5)]
    b = [n.coffee_id for n in repo.neighbors(vec(9), k=5)]
    assert a == b
    assert a.index(ids[0]) < a.index(ids[1])


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
