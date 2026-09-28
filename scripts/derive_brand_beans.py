"""Turn the OFFICIAL franchise bean facts (data/curated/brand_beans_official.yaml) into acidity/body/sweetness/
flavor-tag values for data/curated/brands.yaml (docs/adr/0012-official-brand-beans.md).

Per bean (house and decaf of each brand), in priority order (flavor tags: the brand's own flavor words
first -- official_word_tags -- and the tag model only when the text names none):
  1. official_gauge        -- the brand publishes 1~5 acidity/body/sweetness numbers: taken as-is.
  2. official_notes_model  -- the brand publishes a taste description: the SAME path an unknown bean takes in
     app/graphs/analyze_bean.py, minus the neighbours: the description is embedded as a tag-free query
     (pipeline.embed.embedding_text(..., include_tags=False), input_type="query"), the full-variant learned
     attribute/tag models (app/core/attrmodel.py, app/core/tagmodel.py) predict from it, and explicit cues in
     the official text itself (app/core/textcues.py: "묵직한 바디" → body 4.5, "캬라멜" → caramelized ...)
     outrank the model, exactly like app/core/predict.py::with_text_cues.
  3. estimate              -- no official taste description (bot-blocked site, menu without bean text,
     roast/process only): the existing hand estimate in brands.yaml stays.

Output: data/eval/brand_beans_derived.json (before/after per brand, the text each prediction came from, the
raw model outputs, and label_source per value). brands.yaml is edited by hand from this file -- it carries
comments and per-brand notes a YAML round-trip would destroy.

Usage:
    uv run python scripts/derive_brand_beans.py --before <brands.yaml as it was before>   # e.g. git show <rev>:...
"before" is only for the report; estimate-only beans keep whatever the CURRENT brands.yaml holds.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.attrmodel import AttrModel  # noqa: E402
from app.core.tagmodel import TagModel  # noqa: E402
from app.core.textcues import attr_cues, ko_vocab_from_tag_ko  # noqa: E402
from app.models import ATTRS  # noqa: E402
from app.repo import Repo  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.embed import embedding_text  # noqa: E402
from pipeline.llm import embedder_for  # noqa: E402
from pipeline.records import CoffeeRecord  # noqa: E402

OFFICIAL = settings.CURATED_DIR / "brand_beans_official.yaml"
BRANDS = settings.CURATED_DIR / "brands.yaml"
OUT = settings.EVAL_DIR / "brand_beans_derived.json"
MAX_TAGS = 3            # brand profiles carry 2~3 tags (the card shows 3)
MODEL_TAG_MIN_P = 0.5   # model-only tags (no flavor word in the official text) need at least this probability
GENERIC_TAGS = {"sweet aromatics", "overall sweet"}   # SCA umbrella nodes: say nothing a guest can taste
BASIC_TASTE_TAGS = {"sour", "bitter"}                  # 신맛/쓴맛: attributes (acidity cue), not flavors


def official_word_tags(text: str, vocab: dict[str, str]) -> list[str]:
    """Flavor words the brand itself wrote, mapped to taxonomy tags with the SAME Korean vocabulary the rule
    mapper uses (app/core/textcues.ko_vocab_from_tag_ko). The rule mapper (pipeline.enrich.rule_tags) only fires
    on note-LIST shaped text, and official copy is prose ("시나몬, 브라운슈가, 다크초콜릿의 단맛과 ..."), so this
    scans the prose for vocabulary words directly, keeping the longest match at each position ("다크초콜릿" wins
    over the "초콜릿" inside it). Matching is on the text AS WRITTEN -- stripping spaces first invents words
    across word boundaries ("크레마와 인상" → "와인"). Basic tastes (신맛/쓴맛 → sour/bitter) are left to the
    attribute cues; they are not flavors a card should list. Order = first appearance in the text."""
    hits: list[tuple[int, int, str]] = []
    for term, tag in vocab.items():
        if tag in BASIC_TASTE_TAGS:
            continue
        start = text.find(term)
        while start != -1:
            hits.append((start, start + len(term), tag))
            start = text.find(term, start + 1)
    kept: list[tuple[int, int, str]] = []
    for h in sorted(hits, key=lambda h: (h[0], -(h[1] - h[0]))):
        if not any(k[0] <= h[0] and h[1] <= k[1] for k in kept):
            kept.append(h)
    return list(dict.fromkeys(tag for _, _, tag in sorted(kept)))


def half_step(v: float) -> float:
    """Brand profiles are shown to guests ("산미 3.5"), so round to the nearest 0.5 within [1, 5]."""
    return min(5.0, max(1.0, round(v * 2) / 2))


def official_text(bean: dict) -> str:
    """Only the brand's own words: taste notes, then the origin/blend line (it carries taste words too,
    e.g. 폴바셋 '풍부한 바디 ... 화사한 산미의 에티오피아')."""
    parts = list(bean.get("notes") or [])
    if bean.get("origins"):
        parts.append(bean["origins"])
    return " ".join(parts)


def query_text(brand_name: str, bean: dict, is_decaf: bool) -> str:
    """The tag-free query text, built exactly like a catalogue bean's (pipeline.embed.embedding_text)."""
    c = CoffeeRecord(key="brand-bean", name=f"{brand_name} {bean.get('bean_name') or ''}".strip(),
                     roast_level=bean.get("roast_level"), is_decaf=is_decaf,
                     decaf_process=bean.get("process") if is_decaf else None,
                     flavor_summary=official_text(bean), source="brand_official", collected_at="")
    return embedding_text(c, None, include_tags=False)


def derive(bean: dict, current: dict, brand_name: str, is_decaf: bool, embed, attr_model: AttrModel,
           tag_model: TagModel, tag_to_cat: dict, tag_ko: dict) -> dict:
    gauges = bean.get("gauges")
    if gauges:
        vals = {a: float(gauges[a]) if gauges.get(a) is not None else current[a] for a in ATTRS}
        src = {a: "official_gauge" if gauges.get(a) is not None else "estimate" for a in ATTRS}
        return {"values": {**vals, "flavor_tags": current["flavor_tags"]},
                "label_source": {**src, "flavor_tags": "estimate"}, "basis": {}}
    text = official_text(bean)
    if bean.get("status") != "official" or not text:
        return {"values": dict(current), "label_source": {k: "estimate" for k in (*ATTRS, "flavor_tags")},
                "basis": {"reason": bean.get("reason")}}

    q = query_text(brand_name, bean, is_decaf)
    vec = embed(q)
    model_attrs = attr_model.predict(vec)
    model_tags = tag_model.tags(vec)
    cues = attr_cues(text)
    rule_tags = official_word_tags(text, ko_vocab_from_tag_ko(tag_to_cat, tag_ko))

    values, basis = {}, {}
    for a in ATTRS:
        if a in cues:
            values[a] = half_step(cues[a][0])
            basis[a] = f"cue '{cues[a][1]}' → {cues[a][0]}"
        else:
            values[a] = half_step(model_attrs[a])
            basis[a] = f"model {model_attrs[a]}"
    # the brand's own flavor words win outright; only when it names none does the tag model speak, and then
    # only confidently and never with an umbrella node -- a card must not claim "체리" the brand never wrote
    if rule_tags:
        tags = rule_tags[:MAX_TAGS]
    else:
        tags = [t for t, p in model_tags
                if p >= MODEL_TAG_MIN_P and t not in GENERIC_TAGS | BASIC_TASTE_TAGS][:MAX_TAGS]
    basis["flavor_tags"] = {"from_text": rule_tags, "model": [(t, round(p, 3)) for t, p in model_tags]}
    values["flavor_tags"] = tags
    return {"values": values, "label_source": {k: "official_notes_model" for k in (*ATTRS, "flavor_tags")},
            "basis": basis, "query_text": q, "model_attrs": model_attrs}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, default=BRANDS, help="brands.yaml to report as the 'before' values")
    args = ap.parse_args()
    official = {b["brand"]: b for b in yaml.safe_load(OFFICIAL.read_text(encoding="utf-8"))}
    brands = yaml.safe_load(BRANDS.read_text(encoding="utf-8"))
    baseline = {b["key"]: b for b in yaml.safe_load(args.before.read_text(encoding="utf-8"))}
    attr_model, tag_model = AttrModel.load(), TagModel.load()
    if attr_model is None or tag_model is None:
        raise SystemExit("full-variant attr/tag models are required (config/attr_model.json, config/tag_model.json)")
    embedder = embedder_for()
    embed = lambda text: embedder.embed([text], input_type="query")[0]  # noqa: E731
    repo = Repo(settings.DATABASE_URL)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
    finally:
        repo.close()

    out = []
    for b in brands:
        o = official.get(b["key"])
        if o is None:
            raise SystemExit(f"{b['key']} missing from {OFFICIAL.name}")
        row = {"brand": b["key"], "name": b["name"]}
        for slot, field, decaf in (("house", "bean", False), ("decaf", "decaf_bean", True)):
            current = {k: v for k, v in (b.get(field) or {}).items() if k in (*ATTRS, "flavor_tags")}
            if not current:
                continue
            before = {k: v for k, v in (baseline.get(b["key"], {}).get(field) or {}).items()
                      if k in (*ATTRS, "flavor_tags")} or current
            d = derive(o[slot], current, b["name"], decaf, embed, attr_model, tag_model, tag_to_cat, tag_ko)
            row[slot] = {"bean_name": o[slot].get("bean_name"), "source_url": o[slot].get("source_url"),
                         "verified_at": o[slot].get("verified_at"), "status": o[slot].get("status"),
                         "before": before, **d, "card_note": o[slot].get("card_note")}
            after = d["values"]
            print(f"{b['name']:8s} {slot:5s} {o[slot].get('status'):11s} "
                  + " ".join(f"{a[:3]} {before[a]}→{after[a]}" for a in ATTRS)
                  + f" tags {before['flavor_tags']}→{after['flavor_tags']}  [{d['label_source']['acidity']}]")
        out.append(row)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({embedder.requests} embedding requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
