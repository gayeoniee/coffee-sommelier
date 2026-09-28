"""shopify_gauged: Shopify roasters' own intensity profiles as facts-only labelled beans (ADR 0013)."""
import json

import httpx

from pipeline.collect.web import ShopifyGaugedCollector
from pipeline.http import PoliteClient
from pipeline.normalize.shopify_gauged import (
    intensity_labels, lexicon_value, normalize_shopify_gauged, note_words, parse_product,
)

# Shaped like the real stores' products.json (tags, product_type, body_html); texts are made up.
SHOPS = [
    {"domain": "hippo.test", "roaster": "Hippo", "product_types": ["Coffee"],
     "tag_labels": {"VIBRANT & BRIGHT": {"acidity": "bright"}, "Mellow & Balanced": {"acidity": "mellow"}}},
    {"domain": "intel.test", "roaster": "Intel", "product_types": ["Coffee"],
     "tag_labels": {"Roast Type: Bright": {"acidity": "bright"}, "Roast Type: Comforting": {"acidity": "comforting"}}},
    {"domain": "ozone.test", "roaster": "Ozone", "product_types": ["coffee", "merchandise"], "require_tag": "type:Coffee",
     "tag_labels": {"quiz-bold": {"acidity": "bold", "body": "bold"}}},
    {"domain": "volc.test", "roaster": "Volc", "product_types": ["Coffee"], "exclude_tags": ["Flavored"],
     "tag_labels": {"Low Acid": {"acidity": "low"}}},
    {"domain": "supreme.test", "roaster": "Supreme"},
    {"domain": "fresh.test", "roaster": "Fresh", "product_type_pattern": "^RST"},
]
BY = {s["domain"]: s for s in SHOPS}

HIPPO = {"handle": "brazil-estrela", "title": "Brazil, Estrela", "product_type": "Coffee",
         "tags": ["Coffee Bags", "VIBRANT & BRIGHT"],
         "body_html": "<p>Producer</p><p>Someone</p><p>Cultivar</p><p>Paraiso MG2</p><p>Process</p><p>Natural</p>"
                      "<p>Altitude</p><p>1,150</p><p>masl</p><p>Expect notes of Yellow Plum, Nectarine, Almond.</p>"
                      "<p>Long story about the farm.</p>"}


def test_lexicon_maps_intensity_words_to_1_5():
    assert lexicon_value("acidity", "Bright & Vibrant") == 4.0
    assert lexicon_value("acidity", "low") == 1.5
    assert lexicon_value("body", "full-bodied and smooth") == 3.5        # mean of full-bodied (4) and smooth (3)
    assert lexicon_value("body", "medium to full") == 3.5
    assert lexicon_value("body", "tea-like") == 1.5
    assert lexicon_value("acidity", "wonderful") is None


def test_profile_tag_and_labelled_lines():
    assert intensity_labels(HIPPO, BY["hippo.test"]) == {"acidity": 4.0}
    supreme = {"title": "Filter", "tags": [], "body_html": "<p>Acidity</p><p>Crisp</p><p>Body</p><p>Silky</p>"}
    assert intensity_labels(supreme, BY["supreme.test"]) == {"acidity": 4.0, "body": 2.0}
    numeric = {"title": "x", "tags": [], "body_html": "<p>Acidity: 4</p><p>Body: 2</p>"}
    assert intensity_labels(numeric, BY["supreme.test"]) == {"acidity": 4.0, "body": 2.0}
    fresh = {"title": "x", "tags": [], "body_html": "<p>Roast Body</p><p>: Mild</p><p>Roast Level</p><p>: Medium</p>"}
    assert intensity_labels(fresh, BY["fresh.test"]) == {"body": 2.0}


def test_cup_of_excellence_quality_scores_are_not_intensity():
    ozone = {"title": "Costa Rica", "product_type": "coffee", "tags": ["type:Coffee", "quiz-bold"],
             "body_html": "<p>Sweetness:</p><p>6.5/8</p><p>Acidity:</p><p>6/8</p><p>Mouthfeel:</p><p>6/8</p>"}
    assert intensity_labels(ozone, BY["ozone.test"]) == {"acidity": 2.0, "body": 4.0}   # the quiz tag only


def test_conflicting_profile_tags_drop_the_attribute():
    bundle = {"title": "Blend", "tags": ["Roast Type: Bright", "Roast Type: Comforting"], "body_html": ""}
    assert intensity_labels(bundle, BY["intel.test"]) == {}


def test_parse_product_keeps_facts_only():
    r = parse_product(HIPPO, BY["hippo.test"])
    assert r["origin_country"] == "Brazil" and r["process"] == "natural"
    assert r["altitude_m"] == 1150 and r["variety"] == "Paraiso MG2"
    assert r["notes"] == ["Yellow Plum", "Nectarine", "Almond"]
    assert r["labels"] == {"acidity": 4.0}


def test_origin_only_from_explicit_country_tags():
    blend = {"handle": "otono", "title": "Otono Blend", "product_type": "Coffee", "body_html": "<p>A blend.</p>",
             "tags": ["Related: ethiopia-kirite-washed", "Roast Type: Comforting"]}
    single = {**blend, "title": "Alaka", "tags": ["Country: Ethiopia", "Roast Type: Bright"]}
    assert parse_product(blend, BY["intel.test"])["origin_country"] is None
    assert parse_product(single, BY["intel.test"])["origin_country"] == "Ethiopia"


def test_non_bean_products_and_unlabelled_beans_are_skipped():
    shop = BY["volc.test"]
    base = {"product_type": "Coffee", "tags": ["Low Acid"], "body_html": "<p>Flavor Notes:</p><p>Cocoa, Nuts</p>"}
    assert parse_product({**base, "title": "Low Acid House Blend"}, shop)["labels"] == {"acidity": 1.5}
    assert parse_product({**base, "title": "Pumpkin Spice", "tags": ["Low Acid", "Flavored"]}, shop) is None
    assert parse_product({**base, "title": "Coffee Pods Variety Pack"}, shop) is None
    assert parse_product({**base, "title": "Sumatra", "tags": []}, shop) is None          # no intensity label
    assert parse_product({**base, "title": "Mug", "product_type": "Merch"}, shop) is None
    assert parse_product({"title": "Kenya", "product_type": "merchandise", "tags": ["quiz-bold"]},
                         BY["ozone.test"]) is None                                        # require_tag type:Coffee
    assert parse_product({"title": "Kenya", "product_type": "CPD | FRC", "tags": [],
                          "body_html": "<p>Roast Body: Bold</p>"}, BY["fresh.test"]) is None   # pods, not RST


def test_note_words():
    assert note_words("Tasting Notes:\nApple Pie, Hibiscus, Lemon Sherbet\nAcidity: 4") == \
        ["Apple Pie", "Hibiscus", "Lemon Sherbet"]
    assert note_words("Cupping Notes\n: cocoa, cherry, creamy") == ["cocoa", "cherry", "creamy"]
    assert note_words("A coffee with a story.") == []


def test_normalize_writes_rounded_labels_with_source_and_no_prose(tmp_path):
    (tmp_path / "hippo.test.json").write_text(json.dumps({"products": [HIPPO, HIPPO]}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    n = normalize_shopify_gauged(tmp_path, "2026-09-28", shops=SHOPS)
    [c] = n.coffees                                                       # duplicate handle collapsed
    assert c.key == "shopify_gauged:hippo.test:brazil-estrela" and c.roaster == "Hippo"
    assert (c.acidity, c.body, c.sweetness) == (4, None, None)
    assert c.attr_label_source == {"acidity": "roaster_profile"}
    assert c.flavor_summary == "Yellow Plum, Nectarine, Almond" and c.source_url == "https://hippo.test/products/brazil-estrela"
    assert n.reviews == []                                                # facts only: no description text


def test_half_step_labels_round_up(tmp_path):
    p = {"handle": "low", "title": "Low", "product_type": "Coffee", "tags": ["Low Acid"], "body_html": ""}
    (tmp_path / "volc.test.json").write_text(json.dumps({"products": [p]}), encoding="utf-8")
    [c] = normalize_shopify_gauged(tmp_path, "2026-09-28", shops=SHOPS).coffees
    assert c.acidity == 2                                                 # 1.5 -> 2 (round half up, like gauges)


def test_collector_skips_a_store_that_disallows_products_json(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            body = "User-agent: *\nDisallow: /products" if request.url.host == "blocked.test" else ""
            return httpx.Response(200, text=body)
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"products": [{"id": 1, "handle": "a"}] if page == 1 else []})

    http = PoliteClient(delay=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    files = ShopifyGaugedCollector(domains=("blocked.test", "ok.test")).collect(tmp_path, http)
    assert [f.name for f in files] == ["ok.test.json"]
