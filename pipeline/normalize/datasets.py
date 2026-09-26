import json
import re
from pathlib import Path

import pandas as pd
import yaml

from pipeline import settings
from pipeline.normalize import Normalized
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode
from pipeline.rules import (
    clean, detect_decaf, join_text, normalize_country, normalize_process, normalize_roast,
    num, opt_int, process_from_text, to_quintile,
)


def _find(snap: Path, filename: str) -> Path | None:
    hits = sorted(snap.rglob(filename))
    return hits[0] if hits else None


def _records(path: Path) -> list[dict]:
    return pd.read_csv(path, dtype=str).to_dict("records")


def _empty(v) -> bool:
    return v is None or v == "" or v == {}


def _fill_missing(base: dict | None, new: dict) -> dict:
    if base is None:
        return new
    for k, v in new.items():
        if _empty(base.get(k)) and not _empty(v):
            base[k] = v
    return base


def _subs(r: dict, keys) -> dict[str, float]:
    return {k: v for k in keys if (v := num(r.get(k))) is not None}


# --- coffeereview (3 Kaggle scrapes of coffeereview.com) ------------------
def normalize_coffeereview(snap: Path, collected_at: str) -> Normalized:
    rows: dict[str, dict] = {}

    if p := _find(snap, "reviews_feb_2023.csv"):
        for r in _records(p):
            url = clean(r.get("url"))
            if not url:
                continue
            rows[url] = {
                "url": url, "name": clean(r.get("title")), "roaster": clean(r.get("roaster")),
                "origin": clean(r.get("coffee_origin")), "roast": clean(r.get("roast_level")),
                "acid": num(r.get("acidity_structure")), "body": num(r.get("body")), "rating": num(r.get("rating")),
                "summary": clean(r.get("blind_assessment")),
                "text": join_text(r.get("blind_assessment"), r.get("notes"), r.get("bottom_line")),
                "subs": _subs(r, ("aroma", "flavor", "aftertaste", "with_milk")),
            }

    if p := _find(snap, "coffee_clean.csv"):
        for r in _records(p):
            url = clean(r.get("slug"))
            if not url:
                continue
            new = {
                "url": url, "name": clean(r.get("name")), "roaster": clean(r.get("roaster")),
                "origin": clean(r.get("origin")), "roast": clean(r.get("roast")),
                "acid": num(r.get("acid")), "body": num(r.get("body")), "rating": num(r.get("rating")),
                "summary": clean(r.get("desc_1")), "text": join_text(r.get("desc_1"), r.get("desc_2"), r.get("desc_3")),
                "subs": _subs(r, ("aroma", "flavor", "aftertaste", "with_milk")),
            }
            rows[url] = _fill_missing(rows.get(url), new)

    if p := _find(snap, "coffee_analysis.csv"):
        index = {((v["roaster"] or "").lower(), (v["name"] or "").lower()): k for k, v in rows.items()}
        for r in _records(p):
            ident = ((clean(r.get("roaster")) or "").lower(), (clean(r.get("name")) or "").lower())
            new = {
                "url": None, "name": clean(r.get("name")), "roaster": clean(r.get("roaster")),
                "origin": ", ".join(x for x in (clean(r.get("origin_1")), clean(r.get("origin_2"))) if x) or None,
                "roast": clean(r.get("roast")), "acid": None, "body": None, "rating": num(r.get("rating")),
                "summary": clean(r.get("desc_1")), "text": join_text(r.get("desc_1"), r.get("desc_2"), r.get("desc_3")),
                "subs": {},
            }
            if ident in index:
                rows[index[ident]] = _fill_missing(rows[index[ident]], new)
            else:
                rows[f"{ident[0]}|{ident[1]}"] = new

    out = Normalized()
    if not rows:
        return out
    items = list(rows.items())
    df = pd.DataFrame([v for _, v in items])
    acid_q, body_q = to_quintile(df["acid"]), to_quintile(df["body"])
    for i, (rk, v) in enumerate(items):
        key = f"coffeereview:{rk}"
        is_decaf, decaf_process = detect_decaf(v["name"], v["text"])
        out.coffees.append(CoffeeRecord(
            key=key, name=v["name"] or "(unknown)", roaster=v["roaster"],
            origin_country=normalize_country(v["origin"]), origin_region=v["origin"],
            process=process_from_text(v["text"]), roast_level=normalize_roast(v["roast"]),
            is_decaf=is_decaf, decaf_process=decaf_process,
            acidity=opt_int(acid_q.iloc[i]), body=opt_int(body_q.iloc[i]),
            flavor_summary=v["summary"], source="coffeereview_kaggle", source_url=v["url"],
            collected_at=collected_at,
        ))
        if v["text"]:
            out.reviews.append(ReviewRecord(
                key=f"review:{key}", coffee_key=key, text=v["text"], rating=v["rating"], sub_scores=v["subs"],
                source="coffeereview_kaggle", source_url=v["url"], collected_at=collected_at,
            ))
    return out


# --- CQI -------------------------------------------------------------------
_CQI_2018 = {"country": "Country.of.Origin", "region": "Region", "farm": "Farm.Name",
             "process": "Processing.Method", "acidity": "Acidity", "body": "Body",
             "owner": "Owner", "company": "Company"}
_CQI_ROBUSTA = {**_CQI_2018, "acidity": "Salt...Acid", "body": "Mouthfeel"}
_CQI_2023 = {"country": "Country of Origin", "region": "Region", "farm": "Farm Name",
             "process": "Processing Method", "acidity": "Acidity", "body": "Body",
             "owner": "Owner", "company": "Company"}
_CQI_FILES = {
    "arabica_2018.csv": (_CQI_2018, "https://github.com/jldbc/coffee-quality-database"),
    "robusta_2018.csv": (_CQI_ROBUSTA, "https://github.com/jldbc/coffee-quality-database"),
    "arabica_2023.csv": (_CQI_2023, "https://github.com/fatih-boyar/coffee-quality-data-CQI"),
}


def normalize_cqi(snap: Path, collected_at: str) -> Normalized:
    rows = []
    for filename, (cols, url) in _CQI_FILES.items():
        p = snap / filename
        if not p.exists():
            continue
        for i, r in enumerate(_records(p)):
            g = {k: clean(r.get(c)) for k, c in cols.items()}
            rows.append({"key": f"cqi:{p.stem}:{i}", "url": url, **g,
                         "acid": num(g["acidity"]), "body_n": num(g["body"])})
    out = Normalized()
    if not rows:
        return out
    df = pd.DataFrame(rows)
    acid_q, body_q = to_quintile(df["acid"]), to_quintile(df["body_n"])
    for i, v in enumerate(rows):
        is_decaf, decaf_process = detect_decaf(v["owner"], v["company"], v["farm"])
        out.coffees.append(CoffeeRecord(
            key=v["key"], name=" ".join(x for x in (v["country"], v["region"], v["farm"]) if x) or "CQI sample",
            origin_country=normalize_country(v["country"]), origin_region=v["region"],
            process=normalize_process(v["process"]), is_decaf=is_decaf, decaf_process=decaf_process,
            acidity=opt_int(acid_q.iloc[i]), body=opt_int(body_q.iloc[i]),
            source="cqi", source_url=v["url"], collected_at=collected_at,
        ))
    return out


# --- RoasterDB sample --------------------------------------------------------
def parse_sca_nodes(s: str) -> list[str]:
    tags: list[str] = []
    for part in re.split(r"[;|]", s or ""):
        leaf = part.split(">")[-1].strip().lower()
        if leaf and leaf not in tags:
            tags.append(leaf)
    return tags[:6]


def normalize_roasterdb(snap: Path, collected_at: str) -> Normalized:
    out = Normalized()
    p = snap / "roasterdb_sample.csv"
    if not p.exists():
        return out
    for r in _records(p):
        notes = clean(r.get("tasting_notes_sca_nodes"))
        title = clean(r.get("title")) or "(unknown)"
        is_decaf, decaf_process = detect_decaf(title, notes)
        out.coffees.append(CoffeeRecord(
            key=f"roasterdb:{clean(r.get('product_id'))}", name=title, roaster=clean(r.get("source_roaster")),
            origin_country=normalize_country(r.get("origin_country")), origin_region=clean(r.get("origin_region")),
            process=normalize_process(r.get("process_method")), roast_level=normalize_roast(r.get("roast_level")),
            is_decaf=is_decaf, decaf_process=decaf_process, flavor_tags=parse_sca_nodes(notes or ""),
            flavor_summary=notes, source="roasterdb", source_url=clean(r.get("source_url")),
            collected_at=collected_at,
        ))
    return out


# --- Korean roastery facts (pipeline/collect/roasters_kr.py) ------------------
# Roast labels these shops use that the shared normalize_roast() doesn't know (city/full city/french scale).
# Checked in order, so a range like "시티 or 풀시티" lands on the darker end.
_KO_ROAST = [
    ("medium-dark", re.compile(r"풀\s*시티|중강배전", re.I)),
    ("dark", re.compile(r"프렌치|웰던|well[\s-]?done", re.I)),
    ("medium", re.compile(r"시티|중간\s*볶음", re.I)),
]
_BLEND_SEP = re.compile(r"[,·]")


def roasters_kr_roast(label) -> str | None:
    t = clean(label)
    if not t:
        return None
    return next((name for name, pat in _KO_ROAST if pat.search(t)), None) or normalize_roast(t)


def normalize_roasters_kr(snap: Path, collected_at: str) -> Normalized:
    """Facts-only bean records (no prose): the flavor note words become the enrichment text (flavor_summary)."""
    out = Normalized()
    p = snap / "beans.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        name = clean(r.get("name")) or "(unknown)"
        raw_country = clean(r.get("origin_country"))
        is_decaf, decaf_process = detect_decaf(name, r.get("decaf_process"), "decaf" if r.get("is_decaf") else None)
        notes = [n for n in (clean(x) for x in r.get("flavor_notes") or []) if n]
        region = clean(r.get("origin_region"))
        if region is None and raw_country and _BLEND_SEP.search(raw_country):
            region = raw_country                 # blend: keep every origin; origin_country is the first one
        out.coffees.append(CoffeeRecord(
            key=f"roasters_kr:{r['key']}", name=name, roaster=clean(r.get("roaster")),
            origin_country=normalize_country(raw_country), origin_region=region,
            process=normalize_process(r.get("process")), roast_level=roasters_kr_roast(r.get("roast_level")),
            is_decaf=is_decaf, decaf_process=decaf_process,
            flavor_summary=", ".join(notes) or None, source="roasters_kr", source_url=clean(r.get("product_url")),
            collected_at=clean(r.get("collected_at")) or collected_at,
        ))
    return out


# --- SCA flavor wheel ------------------------------------------------------
def normalize_sca(snap: Path, collected_at: str, ko_path: Path | None = None) -> Normalized:
    out = Normalized()
    p = snap / "sca_coffee_flavors.json"
    if not p.exists():
        return out
    ko_path = ko_path or settings.CURATED_DIR / "sca_ko.yaml"
    ko = yaml.safe_load(ko_path.read_text(encoding="utf-8")) if ko_path.exists() else {}

    def walk(node, path: str, level: int) -> None:
        if isinstance(node, dict):
            children = list(node.items())
        elif isinstance(node, list):
            children = [(x, None) for x in node]
        else:
            return
        for name, child in children:
            here = f"{path}>{name}" if path else name
            out.taxonomy.append(TaxonomyNode(
                key=f"sca:{here}", parent_key=f"sca:{path}" if path else None,
                level=level, name_en=name, name_ko=ko.get(here),
            ))
            if child is not None:
                walk(child, here, level + 1)

    walk(json.loads(p.read_text(encoding="utf-8")), "", 1)
    return out
