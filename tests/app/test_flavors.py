from app.core.flavors import load_tag_ko_extra, merge_tag_ko


def test_load_tag_ko_extra_covers_the_off_wheel_tags_found_on_the_dev_db():
    # These are the tags Fix B found on the dev DB with no flavor_taxonomy.name_ko (2026-09-27): SELECT DISTINCT
    # t FROM coffees, unnest(flavor_tags) t WHERE active, minus flavor_taxonomy.name_en (case-insensitive).
    expected = {"caramel", "milk chocolate", "tropical fruit", "almond", "hibiscus", "bergamot",
                "graham cracker", "pineapple", "orange blossom", "pecan", "passion fruit", "peanut",
                "tangerine", "mango", "cocoa nibs"}
    extra = load_tag_ko_extra()
    assert expected <= extra.keys()
    assert extra["milk chocolate"] == "밀크 초콜릿"
    assert all(isinstance(v, str) and v for v in extra.values())    # every entry actually has a Korean value


def test_merge_tag_ko_keeps_taxonomy_names_on_conflict():
    taxonomy_ko = {"lemon": "레몬"}
    extra = {"lemon": "리몬(WRONG)", "milk chocolate": "밀크 초콜릿"}
    merged = merge_tag_ko(taxonomy_ko, extra)
    assert merged == {"lemon": "레몬", "milk chocolate": "밀크 초콜릿"}     # taxonomy wins, extra fills the gap
