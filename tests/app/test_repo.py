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
    items = {i.name: i for i in repo.brand_items("brand:sb", want_decaf=True)}
    am = items["아메리카노"]
    assert (am.order_decaf, am.decaf_surcharge_krw, am.brand, am.source) == (True, 300, "스타벅스", "brand_bean")
    assert am.tags == ("caramelized",)                       # decaf bean attributes used
    assert items["카페 라떼"].is_milk is True
    plain = {i.name: i for i in repo.brand_items("brand:sb", want_decaf=False)}
    assert plain["아메리카노"].order_decaf is False and plain["아메리카노"].tags == ("chocolate",)
    synthetic = repo.brand_items("brand:tw", want_decaf=True)   # brand without menu rows
    assert [i.name for i in synthetic] == ["아메리카노", "카페라떼"]
    assert all(i.decaf_option and i.menu_item_id is None for i in synthetic)
    assert repo.brand_items("brand:none", want_decaf=False) == []
    assert repo.get_menu_item(am.menu_item_id, want_decaf=True).order_decaf is True


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
