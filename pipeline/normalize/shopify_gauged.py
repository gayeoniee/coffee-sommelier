"""Facts-only bean records from Shopify roasters that publish a structured taste-intensity label
(docs/adr/0013-open-labels-weak-supervision.md).

A 2026-09 survey of ~90 Shopify coffee stores (US/UK/EU/AU/NZ/CA) found that almost none put a numeric
gauge in `/products.json`; what a handful DO publish is the roaster's own coarse profile for each bean --
a profile tag ("VIBRANT & BRIGHT", "Roast Type: Comforting", "quiz-bold", "Low Acid") or a labelled line
("Acidity:\\nCrisp", "Body: full-bodied and smooth", "Roast Body: Mild", "Acidity: 4"). Those are human labels
the roaster assigned to its own product, so -- like the Korean roasters' gauges (ADR 0011) -- they are kept as
facts and mapped onto our 1-5 scale by ONE documented lexicon (`ACIDITY_WORDS`/`BODY_WORDS`/`SWEETNESS_WORDS`).
Which tag means what is per-store config (config/sources.yaml `shopify_gauged`), so every mapping is auditable.

Only facts are stored: title, roaster, origin, process, roast, decaf, labelled altitude/variety, the bean's own
note-word list, the intensity labels -- never the description prose (no ReviewRecord). Products without any
intensity label are skipped (they add nothing this source exists for). Cup-of-Excellence style "Acidity: 6/8"
scores (Ozone/Has Bean) are QUALITY scores, not intensity, and are ignored (ADR 0011 showed quality scores
don't stand in for intensity).
"""
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from pipeline import settings
from pipeline.normalize import Normalized
from pipeline.records import CoffeeRecord
from pipeline.rules import (
    clean, detect_decaf, normalize_country, normalize_process, normalize_roast, parse_altitude_m, process_from_text,
    round_half_up,
)

ATTRS = ("acidity", "body", "sweetness")
LABEL_SOURCE = "roaster_profile"

# One lexicon for every store. Low/mellow -> 1-2, medium/balanced -> 3, bright/high/juicy -> 4-5;
# light/tea-like body -> 1-2, medium/smooth -> 3, full/heavy/syrupy -> 4-5. A phrase scores the mean of the
# lexicon words it contains ("full-bodied and smooth" -> (4 + 3) / 2 = 3.5); no lexicon word -> no label.
ACIDITY_WORDS = {
    "very low": 1.0, "low": 1.5, "traditional": 1.5,
    "mellow": 2.0, "mild": 2.0, "soft": 2.0, "gentle": 2.0, "subtle": 2.0, "smooth": 2.0, "comforting": 2.0,
    "bold": 2.0, "refined": 2.5, "rounded": 2.5,
    "medium": 3.0, "moderate": 3.0, "balanced": 3.0, "classic": 3.0,
    "bright": 4.0, "vibrant": 4.0, "lively": 4.0, "crisp": 4.0, "citric": 4.0, "tangy": 4.0, "zesty": 4.0,
    "sparkling": 4.5, "juicy": 4.5, "high": 4.5, "very bright": 5.0,
}
BODY_WORDS = {
    "tea-like": 1.5, "light": 1.5, "delicate": 2.0, "mild": 2.0, "silky": 2.0, "thin": 1.5,
    "medium": 3.0, "smooth": 3.0, "round": 3.0, "rounded": 3.0, "balanced": 3.0, "cradling": 3.0,
    "creamy": 4.0, "full": 4.0, "full-bodied": 4.0, "rich": 4.0, "bold": 4.0, "buttery": 4.0, "velvety": 3.5,
    "heavy": 4.5, "syrupy": 4.5, "viscous": 4.5, "fat": 4.5, "robust": 4.5, "intense": 4.5,
}
SWEETNESS_WORDS = {
    "low": 1.5, "subtle": 2.0, "medium": 3.0, "moderate": 3.0, "sweet": 4.0, "high": 4.5, "very sweet": 5.0,
}
LEXICON = {"acidity": ACIDITY_WORDS, "body": BODY_WORDS, "sweetness": SWEETNESS_WORDS}

# Labelled lines ("Acidity: Crisp", "Acidity\nCrisp", "Roast Body\n: Mild"); the value is one short line.
_LINE_LABELS = {"acidity": "acidity", "body": "body", "roast body": "body", "sweetness": "sweetness"}
_LINE = re.compile(r"(?im)^\s*(acidity|roast body|body|sweetness)\s*:?\s*\n?\s*:?\s*([^\n:]{1,40})\s*$")
_QUALITY = re.compile(r"^\d+(?:\.\d+)?\s*/\s*(?:8|10)\b")       # "6/8", "6.5/10": a cupping QUALITY score
_NUMBER = re.compile(r"^([1-5](?:\.5)?)$")                     # "Acidity: 4" on a 1-5 scale

_FACT = re.compile(r"(?im)^\s*(altitude|elevation|variety|varietal|cultivar|process|processing(?: method)?|"
                   r"country|origin|region|roast(?: level)?)\s*:?\s*\n?\s*:?\s*([^\n]{1,60})$")
_NOTE_LIST = re.compile(
    r"(?is)(?:tasting|cupping|flavou?r|cup)\s+notes?\s*:?\s*\n?\s*:?\s*([^\n.]{3,120})|"
    r"(?:expect\s+)?notes\s+of\s+([^\n.]{3,120})")
_EXCLUDE = re.compile(r"\b(pods?|capsules?|k-?cups?|bundle|trio|duo|sampler|subscription|gift|box|set|pack|"
                      r"collection|flight|instant|cold brew|drip bags?|steeped|tea|mug|merch|flavou?red|"
                      r"green coffee|unroasted|syrup)\b", re.I)
MAX_NOTES = 8


def lexicon_value(attr: str, phrase: str | None) -> float | None:
    """Mean lexicon value of the intensity words in `phrase` (longest match first), or None."""
    t = (phrase or "").lower().replace("–", "-")
    words = LEXICON[attr]
    hits = []
    for w in sorted(words, key=len, reverse=True):
        pat = re.compile(rf"(?<![a-z]){re.escape(w)}(?![a-z])")
        if pat.search(t):
            hits.append(words[w])
            t = pat.sub(" ", t)
    return round(sum(hits) / len(hits), 2) if hits else None


def _tags(p: dict) -> list[str]:
    t = p.get("tags") or []
    return [x.strip() for x in t.split(",")] if isinstance(t, str) else [str(x).strip() for x in t]


def _text(p: dict) -> str:
    return BeautifulSoup(p.get("body_html") or "", "lxml").get_text("\n", strip=True)


def intensity_labels(p: dict, shop: dict) -> dict[str, float]:
    """{attr: 1-5 float} from the store's configured profile tags and any labelled intensity line.
    A tag and a line for the same attribute are averaged; two configured tags that disagree (a bundle
    tagged both "Bright" and "Comforting") drop that attribute."""
    found: dict[str, list[float]] = {}
    tag_map = {k.lower(): v for k, v in (shop.get("tag_labels") or {}).items()}
    tag_vals: dict[str, set[float]] = {}
    for tag in _tags(p):
        for attr, word in (tag_map.get(tag.lower()) or {}).items():
            v = lexicon_value(attr, word)
            if v is not None:
                tag_vals.setdefault(attr, set()).add(v)
    for attr, vals in tag_vals.items():
        if len(vals) == 1:
            found.setdefault(attr, []).append(next(iter(vals)))
    if shop.get("read_lines", True):
        from_lines: set[str] = set()
        for label, value in _LINE.findall(_text(p)):
            attr = _LINE_LABELS[label.lower()]
            value = value.strip()
            if attr in from_lines or _QUALITY.match(value):
                continue                                   # first labelled line per attribute only
            m = _NUMBER.match(value)
            v = float(m.group(1)) if m else lexicon_value(attr, value)
            if v is not None:
                found.setdefault(attr, []).append(v)
                from_lines.add(attr)
    return {a: round(sum(vs) / len(vs), 2) for a, vs in found.items() if vs}


def _facts(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, value in _FACT.findall(text):
        key = label.lower().split()[0]
        out.setdefault({"elevation": "altitude", "varietal": "variety", "cultivar": "variety",
                        "processing": "process", "origin": "country", "region": "country"}.get(key, key),
                       value.strip())
    return out


def note_words(text: str) -> list[str]:
    """The bean's own note-word list ("Tasting Notes: Apple Pie, Hibiscus, Lemon Sherbet",
    "Expect notes of Yellow Plum, Nectarine, Almond.") -- words only, never the sentence around them."""
    m = _NOTE_LIST.search(text)
    if not m:
        return []
    raw = (m.group(1) or m.group(2) or "").replace("|", ",").replace(" and ", ", ").replace("&", ",")
    words = [w.strip(" .;:-") for w in re.split(r"[,;/]", raw)]
    return [w for w in words if w and len(w) <= 30][:MAX_NOTES]


def is_bean_product(p: dict, shop: dict) -> bool:
    """A whole-bean coffee product of this store (config: product_types / product_type_pattern / require_tag /
    exclude_tags), never a pod, bundle, subscription, gift, merch or flavoured coffee."""
    ptype = p.get("product_type") or ""
    if shop.get("product_types") and ptype not in shop["product_types"]:
        return False
    if shop.get("product_type_pattern") and not re.search(shop["product_type_pattern"], ptype):
        return False
    tags = {t.lower() for t in _tags(p)}
    if shop.get("require_tag") and shop["require_tag"].lower() not in tags:
        return False
    if tags & {t.lower() for t in shop.get("exclude_tags") or ()}:
        return False
    return not _EXCLUDE.search(p.get("title") or "")


def parse_product(p: dict, shop: dict) -> dict | None:
    """Facts + intensity labels of one Shopify product, or None (not a bean / no intensity label)."""
    if not is_bean_product(p, shop):
        return None
    labels = intensity_labels(p, shop)
    if not labels:
        return None
    title = clean(p.get("title")) or "(unknown)"
    text = _text(p)
    facts = _facts(text)
    tags = " ".join(_tags(p))
    # only an explicit origin tag ("Country: Ethiopia", "origin:Kenya") -- never "Related: ethiopia-..." handles
    origin_tags = " ".join(t for t in _tags(p) if re.match(r"(country|origin)\s*:", t, re.I))
    is_decaf, decaf_process = detect_decaf(title, text[:600], tags)
    # "Dark Roast", "By Roast_Medium Roast", "roast: Light" -- but not Intelligentsia's "Roast Type: Bright",
    # which is a taste profile (configured as a tag label), not a roast degree
    roast_tags = " ".join(t for t in _tags(p) if re.search(r"roast", t, re.I)
                          and not re.match(r"roast (type|level|profile)\s*:", t, re.I))
    return {
        "name": title,
        "origin_country": normalize_country(facts.get("country")) or normalize_country(title)
        or normalize_country(origin_tags),
        "process": normalize_process(facts.get("process")) or process_from_text(f"{title}\n{text}"),
        "roast_level": normalize_roast(facts.get("roast")) or normalize_roast(roast_tags),
        "is_decaf": is_decaf, "decaf_process": decaf_process,
        "altitude_m": parse_altitude_m(facts.get("altitude")),
        "variety": clean(facts.get("variety")),
        "notes": note_words(text),
        "labels": labels,
    }


def iter_products(snap: Path, shops: list[dict] | None = None):
    """(key, url, shop, parsed) for every labelled bean product in a shopify_gauged snapshot."""
    shops = shops if shops is not None else settings.load_config("sources.yaml")["shopify_gauged"]
    by_domain = {s["domain"]: s for s in shops}
    for f in sorted(snap.glob("*.json")):
        if f.name == "manifest.json" or f.stem not in by_domain:
            continue
        shop = by_domain[f.stem]
        seen = set()
        for p in json.loads(f.read_text(encoding="utf-8"))["products"]:
            handle = p.get("handle")
            if not handle or handle in seen:
                continue
            seen.add(handle)
            parsed = parse_product(p, shop)
            if parsed is not None:
                yield f"shopify_gauged:{f.stem}:{handle}", f"https://{f.stem}/products/{handle}", shop, parsed


def normalize_shopify_gauged(snap: Path, collected_at: str, shops: list[dict] | None = None) -> Normalized:
    out = Normalized()
    for key, url, shop, r in iter_products(snap, shops):
        labels = {a: round_half_up(v) for a, v in r["labels"].items()}
        out.coffees.append(CoffeeRecord(
            key=key, name=r["name"], roaster=shop["roaster"], origin_country=r["origin_country"],
            process=r["process"], roast_level=r["roast_level"], is_decaf=r["is_decaf"],
            decaf_process=r["decaf_process"], **labels,
            attr_label_source={a: LABEL_SOURCE for a in labels},
            altitude_m=r["altitude_m"], variety=r["variety"],
            flavor_summary=", ".join(r["notes"]) or None,
            source="shopify_gauged", source_url=url, collected_at=collected_at,
        ))
    return out
