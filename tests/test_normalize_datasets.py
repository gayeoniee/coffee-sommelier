import json

from pipeline.normalize.datasets import (
    normalize_coffeereview, normalize_cqi, normalize_roasterdb, normalize_roasters_kr, normalize_sca, parse_sca_nodes,
)

PATKLE = (
    "title,rating,acidity_structure,aftertaste,aroma,body,flavor,with_milk,agtron,blind_assessment,"
    "bottom_line,coffee_origin,est_price,notes,review_date,roast_level,roaster,roaster_location,url\n"
    'Bolivia Gesha,93,9,8,9,8,9,,60/78,"Floral. Magnolia, cocoa nib.","Great.","Caranavi, Bolivia",$30,'
    '"Washed process.",January 2023,Medium-Light,Red Rooster,Floyd,https://cr.test/review/a/\n'
    'Decaf Colombia,90,7,8,8,9,8,,55/70,"Cocoa, walnut.","Good decaf.","Huila, Colombia",$18,'
    '"Swiss Water decaffeinated.",March 2022,Medium,Roaster B,Here,https://cr.test/review/b/\n'
)
HANIF = (
    "slug,all_text,rating,roaster,name,location,origin,roast,est_price,review_date,agtron,"
    "aroma,acid,body,flavor,aftertaste,with_milk,desc_1,desc_2,desc_3\n"
    "https://cr.test/review/a/,x,93,Red Rooster,Bolivia Gesha,Floyd,\"Caranavi, Bolivia\",Medium-Light,$30,"
    "Jan 2023,60/78,9,9,8,9,8,,d1,d2,d3\n"
    "https://cr.test/review/c/,x,91,Lu's,Kenya AA,Taipei,\"Nyeri, Kenya\",Light,NT,Oct 2023,58/78,"
    "9,8,7,9,8,,\"Black currant, lemon.\",Washed.,Bright.\n"
)
SCHMOYOTE = (
    "name,roaster,roast,loc_country,origin_1,origin_2,100g_USD,rating,review_date,desc_1,desc_2,desc_3\n"
    "Kenya AA,Lu's,Light,Taiwan,Kenya,,5,91,2023,dup,dup,dup\n"
    "Sweety Blend,A.R.C.,Medium-Light,Hong Kong,Panama,Ethiopia,14,95,2017,\"Chocolaty, vanilla.\",Blend.,Rich.\n"
)


def write_cr(tmp_path):
    for sub, fn, body in [("patkle__x", "reviews_feb_2023.csv", PATKLE),
                          ("hanif__y", "coffee_clean.csv", HANIF),
                          ("schmoyote__z", "coffee_analysis.csv", SCHMOYOTE)]:
        (tmp_path / sub).mkdir()
        (tmp_path / sub / fn).write_text(body, encoding="utf-8")


def test_coffeereview_merges_three_sources(tmp_path):
    write_cr(tmp_path)
    n = normalize_coffeereview(tmp_path, "2026-09-24")
    keys = sorted(c.key for c in n.coffees)
    assert keys == ["coffeereview:a.r.c.|sweety blend", "coffeereview:https://cr.test/review/a/",
                    "coffeereview:https://cr.test/review/b/", "coffeereview:https://cr.test/review/c/"]
    by = {c.key: c for c in n.coffees}
    a = by["coffeereview:https://cr.test/review/a/"]
    assert (a.origin_country, a.process, a.roast_level, a.is_decaf) == ("Bolivia", "washed", "medium-light", False)
    b = by["coffeereview:https://cr.test/review/b/"]
    assert (b.is_decaf, b.decaf_process) == (True, "swiss-water")
    assert all(c.acidity is None or 1 <= c.acidity <= 5 for c in n.coffees)
    assert by["coffeereview:a.r.c.|sweety blend"].origin_country == "Panama"
    assert len(n.reviews) == 4
    assert all(r.coffee_key in by for r in n.reviews)


CQI18 = (
    "Unnamed: 0,Species,Owner,Country.of.Origin,Farm.Name,Company,Region,Variety,Processing.Method,"
    "Acidity,Body,Sweetness\n"
    "1,Arabica,metad,Ethiopia,metad plc,metad co,guji,,Washed / Wet,8.75,8.5,10\n"
    "2,Arabica,x,Mexico,finca,Descafeinadores Mexicano,chiapas,,Natural / Dry,7.0,7.2,10\n"
)
CQI23 = (
    "Unnamed: 0,ID,Country of Origin,Farm Name,Company,Region,Variety,Processing Method,Acidity,Body,Sweetness,Owner\n"
    "0,0,Colombia,Finca El Paraiso,CQU,Cauca,Castillo,Double Anaerobic Washed,8.58,8.25,10,CQU\n"
)


def test_cqi_maps_both_schemas(tmp_path):
    (tmp_path / "arabica_2018.csv").write_text(CQI18, encoding="utf-8")
    (tmp_path / "arabica_2023.csv").write_text(CQI23, encoding="utf-8")
    n = normalize_cqi(tmp_path, "2026-09-24")
    by = {c.key: c for c in n.coffees}
    assert set(by) == {"cqi:arabica_2018:0", "cqi:arabica_2018:1", "cqi:arabica_2023:0"}
    assert by["cqi:arabica_2018:0"].acidity == 5 and by["cqi:arabica_2018:1"].acidity == 2
    assert by["cqi:arabica_2023:0"].process == "anaerobic"
    assert by["cqi:arabica_2018:1"].is_decaf is False  # "Descafeinadores" alone is not the word decaf
    assert all(c.sweetness is None for c in n.coffees)
    assert n.reviews == []


def test_roasterdb(tmp_path):
    (tmp_path / "roasterdb_sample.csv").write_text(
        "product_id,source_roaster,title,origin_country,origin_region,process_method,roast_level,"
        "tasting_notes_sca_nodes,source_url\n"
        "14,3fe,Bolivia Natural,Bolivia,,Natural,Unknown,Sweet > Honey > Honey; Fruity > Berry > Blueberry,https://r.test/p\n",
        encoding="utf-8")
    n = normalize_roasterdb(tmp_path, "2026-09-24")
    c = n.coffees[0]
    assert (c.key, c.process, c.roast_level) == ("roasterdb:14", "natural", None)
    assert c.flavor_tags == ["honey", "blueberry"]


def test_parse_sca_nodes_dedupes():
    assert parse_sca_nodes("A > B > lemon | A > B > Lemon; C > lime") == ["lemon", "lime"]


def test_sca_walks_tree_with_korean(tmp_path):
    (tmp_path / "sca_coffee_flavors.json").write_text(
        json.dumps({"fruity": {"berry": ["blackberry"], "citrus fruit": ["lemon"]}}), encoding="utf-8")
    ko = tmp_path / "ko.yaml"
    ko.write_text("fruity: 과일\nfruity>berry: 베리\n", encoding="utf-8")
    n = normalize_sca(tmp_path, "2026-09-24", ko_path=ko)
    by = {t.key: t for t in n.taxonomy}
    assert set(by) == {"sca:fruity", "sca:fruity>berry", "sca:fruity>berry>blackberry",
                       "sca:fruity>citrus fruit", "sca:fruity>citrus fruit>lemon"}
    assert by["sca:fruity>berry>blackberry"].parent_key == "sca:fruity>berry"
    assert by["sca:fruity>berry>blackberry"].level == 3
    assert by["sca:fruity>berry"].name_ko == "베리"


def _bean(key, **kw):
    base = {"key": key, "site": "s", "roaster": "로스터", "name": key, "origin_country": None, "origin_region": None,
            "origin_farm": None, "process": None, "roast_level": None, "is_decaf": False, "decaf_process": None,
            "flavor_notes": [], "price_krw": 20000, "weight_g": 200, "product_url": f"https://r.test/{key}",
            "collected_at": "2026-09-26"}
    return json.dumps(base | kw, ensure_ascii=False)


def test_roasters_kr_maps_korean_facts(tmp_path):
    (tmp_path / "beans.jsonl").write_text("\n".join([
        _bean("a:1", name="게뎁 첼베사 워시드", origin_country="에티오피아", origin_region="게뎁", process="워시드",
              roast_level="시티(중간볶음) or 풀시티(강한볶음)", flavor_notes=["레몬", "청사과"]),
        _bean("a:2", name="[디카페인] 콜롬비아", origin_country="콜롬비아", is_decaf=True,
              decaf_process="Mountain Water Process", roast_level="Dark"),
        _bean("a:3", name="블렌드", origin_country="브라질산, 콜롬비아산, 인도", process="무산소 내추럴",
              roast_level="시티(중간볶음)", is_decaf=True, decaf_process="Sugarcane Process"),
        _bean("a:4", name="하우스", roast_level="프렌치(강한볶음)"),
    ]) + "\n", encoding="utf-8")
    by = {c.key: c for c in normalize_roasters_kr(tmp_path, "unused").coffees}
    a = by["roasters_kr:a:1"]
    assert (a.source, a.origin_country, a.origin_region, a.process, a.roast_level) == (
        "roasters_kr", "Ethiopia", "게뎁", "washed", "medium-dark")
    assert (a.flavor_summary, a.flavor_tags, a.acidity, a.collected_at) == ("레몬, 청사과", [], None, "2026-09-26")
    b = by["roasters_kr:a:2"]
    assert (b.is_decaf, b.decaf_process, b.roast_level, b.flavor_summary) == (True, "mountain-water", "dark", None)
    c = by["roasters_kr:a:3"]
    assert (c.origin_country, c.origin_region, c.process, c.roast_level) == (
        "Brazil", "브라질산, 콜롬비아산, 인도", "anaerobic", "medium")
    assert (c.is_decaf, c.decaf_process) == (True, "sugarcane-ea")
    assert by["roasters_kr:a:4"].roast_level == "dark"
    assert normalize_roasters_kr(tmp_path / "missing", "x").coffees == []


def test_roasters_kr_gauges_become_labels_with_source(tmp_path):
    (tmp_path / "beans.jsonl").write_text("\n".join([
        _bean("g:1", name="예가체프", origin_country="에티오피아", gauge_acidity=4.5, gauge_body=2.0,
              gauge_sweetness=2.5, gauge_scale="x: 0-5", altitude_m=2000, variety="Heirloom"),
        _bean("g:2", name="블렌드", gauge_acidity=1.0, gauge_scale="x"),
        _bean("g:3", name="무게이지"),
    ]) + "\n", encoding="utf-8")
    by = {c.key: c for c in normalize_roasters_kr(tmp_path, "unused").coffees}
    g1 = by["roasters_kr:g:1"]
    assert (g1.acidity, g1.body, g1.sweetness) == (5, 2, 3)          # half steps round up
    assert g1.attr_label_source == {"acidity": "gauge", "body": "gauge", "sweetness": "gauge"}
    assert (g1.altitude_m, g1.variety) == (2000, "Heirloom")
    g2 = by["roasters_kr:g:2"]
    assert (g2.acidity, g2.body, g2.attr_label_source) == (1, None, {"acidity": "gauge"})
    assert by["roasters_kr:g:3"].attr_label_source == {}


def test_cqi_exposes_altitude_variety_and_quality_label_source(tmp_path):
    (tmp_path / "arabica_2023.csv").write_text(
        "Unnamed: 0,ID,Country of Origin,Farm Name,Company,Region,Variety,Processing Method,Acidity,Body,Altitude,Owner\n"
        "0,0,Colombia,Finca A,CQU,Cauca,Castillo,Washed / Wet,8.58,8.25,1800-2200,CQU\n"
        "1,1,Kenya,Finca B,CQU,Nyeri,SL28,Washed / Wet,8.10,8.00,190164,CQU\n", encoding="utf-8")
    by = {c.key: c for c in normalize_cqi(tmp_path, "2026-09-28").coffees}
    a, b = by["cqi:arabica_2023:0"], by["cqi:arabica_2023:1"]
    assert (a.altitude_m, a.variety) == (2000, "Castillo")
    assert b.altitude_m is None                                       # implausible typo dropped
    assert a.attr_label_source == {"acidity": "cqi_quality", "body": "cqi_quality"}
