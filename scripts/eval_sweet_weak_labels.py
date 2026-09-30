"""Phase 11 pilot: do sweetness WEAK LABELS extracted from roaster prose agree with published sweetness GAUGES
well enough to be worth scaling up (docs/adr/0013-open-labels-weak-supervision.md's pattern, applied to sweetness
phrases/note-words instead of ADR 0013's SCA-tag-only weak labels)?

Validation sets (beans with BOTH a published sweetness gauge and note/description text):
  - roasters_kr:   attr_label_source->>'sweetness' = 'gauge'            (Korean roasters' own strength gauges)
  - shopify_gauged: attr_label_source->>'sweetness' = 'roaster_profile' (overseas stores' own coarse profile, not LLM)
Note text comes from `coffees.flavor_summary` + `flavor_tags` (both DBs). For shopify_gauged this is thin (the
pipeline only keeps the note *words*, not full prose -- ADR 0013), so this script also pulls the raw cached
`body_html` prose from data/raw/shopify_gauged/<date>/<domain>.json for those two beans, matched by product handle.

A separate, NEW, hand-written sweetness lexicon lives in this script only (SWEET_HIGH/MID/LOW/NOTE_WORDS below) --
it is not app/core/weaklabels.py's SWEETNESS tag-push dict, and is never fitted to the gauges it is checked
against. Two variants are scored: "full" (phrases + note words + texture) and "notes_only" (sweet-associated note
words alone: caramel/brown sugar/honey/toffee/molasses/chocolate, KO 캐러멜/흑설탕/꿀/토피/당밀/초콜릿) -- the second
tests whether just the note-word coincidence (already partly known from ADR 0013's SWEETNESS dict) carries the
signal, or whether the extra sweetness-specific phrases add anything.

Ship rule (this pilot only, not a config change): worth scaling up if pooled Spearman >= 0.35 with coverage >= 40%
AND the weak label beats the constant-mean baseline on both +-1 and MAE. Otherwise: not worth it.

Also estimates scale-up YIELD without collecting, from the saved page-1 Shopify scan (49 robots-allowed overseas
stores, C:/.../scratchpad/probe/shopify_scan.json): how many page-1 products' body_html mentioned "sweet" at all.

Writes data/eval/open/phase11_sweet_weak_pilot.json only. No DB writes, no config changes.

    uv run python scripts/eval_sweet_weak_labels.py
"""
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_zenodo_panel import attr_metrics, spearman  # noqa: E402
from scripts.train_feature_model import OPEN_URL  # noqa: E402

SCAN_PATH = Path(
    r"C:/Users/pc/AppData/Local/Temp/claude/C--Users-pc-orca-workspaces-wine-sommelier-rag-hawksbill/"
    r"30cf72fd-9411-4a5f-889d-a58c0a1933b0/scratchpad/probe/shopify_scan.json"
)
SHOPIFY_GAUGED_RAW = ROOT / "data/raw/shopify_gauged/2026-09-28"
OUT = ROOT / "data/eval/open/phase11_sweet_weak_pilot.json"

ROAST_ORDINAL = {"light": 1, "medium-light": 1.5, "medium": 2, "medium-dark": 3, "dark": 4}

# ---- the sweetness lexicon (NEW, scratch, not the shipped weaklabels.py dict) -----------------------------------
# Each entry: compiled regex -> signed push. Order matters only for `matches` bookkeeping, not scoring (pushes sum).
SWEET_HIGH = [
    (r"\bvery\s+sweet\b|\bsuper\s+sweet\b|\bsyrup(?:y)?\b|\bcandy[\s-]?like\b|\bsugary\b|honey[\s-]?like\s+sweetness"
     r"|\bintensely\s+sweet\b|\bincredibly\s+sweet\b|\bsweet\s+but\s+elegant\b|\bsprinkle\s+of\s+\w+\s+sugar\b", 1.6),
]
SWEET_MIDHIGH = [(r"\bsweet(ness)?\b", 0.8)]
SWEET_MID = [(r"\bbalanced\s+sweetness\b|\bmild\s+sweetness\b|\bsubtle\s+sweetness\b|\bgentle\s+sweetness\b", 0.2)]
# "with no sweet mention" guard is enforced in score(): these only fire if no other sweet cue matched at all.
SWEET_LOW = [(r"\bdry\b|\bsavory\b|\bsavoury\b|\bbitter\b|\btart\b|\bbright\b", -0.8)]
NEGATION_RE = re.compile(r"\b(not|isn'?t|without|less|hardly|barely)\b[\w\s]{0,18}$")

KO_HIGH = [
    (r"단맛이?\s*(좋은|풍부한|강한)|(짙은|깊은|진한)\s*단맛|달콤한|달달한|시럽\s*같은|시럽같은", 1.6),
]
KO_LOW = [(r"(옅은|약한|낮은|은은한)\s*단맛|드라이한|쌉쌀한", -1.0)]
KO_MID = [(r"단맛(?!이)", 0.4)]  # bare "단맛" tag/word, not already caught by the KO_HIGH/LOW phrases above
KO_NEGATION_RE = re.compile(r"(너무|과하게)\s*$")  # "너무 달지 않은" etc. -- dampen rather than invert

# sweet-associated NOTE WORDS (EN + KO), used both inside the "full" lexicon and alone as the "notes_only" variant.
# chocolate is flagged uncertain (task step 1's "chocolate?") -- roast-linked, not sweetness-specific; kept but at
# half weight, and reported separately.
NOTE_WORDS = [
    (r"\bcaramel(?:ized)?\b|캐러멜", 0.6),
    (r"\bbrown\s+sugar\b|흑설탕|갈색\s*설탕", 0.6),
    (r"\bhoney\b|꿀", 0.6),
    (r"\btoffee\b|토피", 0.6),
    (r"\bmolasses\b|당밀", 0.6),
    (r"\bchocolate\b|초콜릿|초콜렛", 0.3),  # uncertain: roast-linked as much as sweetness-linked
]


def _negated(text: str, m: re.Match) -> bool:
    before = text[:m.start()]
    return bool(NEGATION_RE.search(before) or KO_NEGATION_RE.search(before))


def score_sweetness(text: str, variant: str = "full") -> tuple[float | None, list[str]]:
    """variant: 'full' (phrases + notes + low-sweetness words) or 'notes_only' (sweet-associated note words alone)."""
    if not text:
        return None, []
    t = text.lower()
    push, hits, any_sweet_word = 0.0, [], False

    def scan(patterns, dampen_on_negation=True):
        nonlocal push, any_sweet_word
        for pat, val in patterns:
            for m in re.finditer(pat, t, re.IGNORECASE):
                v = val
                if dampen_on_negation and val > 0 and _negated(t, m):
                    v = val * 0.15  # "handle negation lightly": dampen, don't flip to negative
                push += v
                hits.append(m.group(0))
                if val > 0:
                    any_sweet_word = True

    if variant == "notes_only":
        scan(NOTE_WORDS)
        if not hits:
            return None, []
        label = 3 + push
        return max(1.0, min(5.0, label)), hits

    scan(SWEET_HIGH)
    scan(SWEET_MIDHIGH)
    scan(SWEET_MID)
    scan(KO_HIGH)
    scan(KO_MID)
    scan(NOTE_WORDS)
    # low-sweetness words only count "with no sweet mention" (task step 1)
    if not any_sweet_word:
        scan(SWEET_LOW, dampen_on_negation=False)
        scan(KO_LOW, dampen_on_negation=False)
    if not hits:
        return None, []
    label = 3 + push
    return max(1.0, min(5.0, label)), hits


# ---- data loading -------------------------------------------------------------------------------------------
def shopify_body_prose(key: str) -> str:
    """key = 'shopify_gauged:<domain>:<handle>' -> plain-text body_html from the cached products.json, or ''."""
    parts = key.split(":", 2)
    if len(parts) != 3:
        return ""
    _, domain, handle = parts
    f = SHOPIFY_GAUGED_RAW / f"{domain}.json"
    if not f.exists():
        return ""
    data = json.loads(f.read_text(encoding="utf-8"))
    for p in data.get("products", []):
        if p.get("handle") == handle:
            html_ = p.get("body_html") or ""
            # strip <style>/<script> chrome first -- some product pages embed a CSS acidity/body/sweetness
            # slider widget (literal text "Sweetness Low High") that is UI, not roaster prose, and would
            # otherwise false-positive match the lexicon.
            html_ = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", html_, flags=re.I | re.S)
            return re.sub(r"<[^>]+>", " ", html_)
    return ""


def load_rows(conn) -> list[dict]:
    rows = conn.execute("""
        select source, key, roaster, roast_level, sweetness,
               coalesce(flavor_summary,'') as flavor_summary, flavor_tags
        from coffees
        where (source = 'roasters_kr' and attr_label_source->>'sweetness' = 'gauge')
           or (source = 'shopify_gauged' and attr_label_source->>'sweetness' = 'roaster_profile')
        order by source, key
    """).fetchall()
    out = []
    for r in rows:
        text = r["flavor_summary"] + " " + " ".join(r["flavor_tags"] or [])
        raw_prose = ""
        if r["source"] == "shopify_gauged":
            raw_prose = shopify_body_prose(r["key"])
            text = (text + " " + raw_prose).strip()
        out.append({**r, "text": text.strip(), "has_raw_prose": bool(raw_prose)})
    return out


# ---- metrics --------------------------------------------------------------------------------------------------
def _recentered(pairs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """ADR 0013's B recipe: the lexicon only knows direction, not the gauges' absolute level, so shift the weak
    label's mean onto the truth's mean (no rescale) before judging it -- same idea, applied per pool here."""
    if not pairs:
        return []
    shift = sum(t for t, _ in pairs) / len(pairs) - sum(p for _, p in pairs) / len(pairs)
    return [(t, max(1.0, min(5.0, p + shift))) for t, p in pairs]


def eval_variant(rows: list[dict], variant: str) -> dict:
    per_source = {}
    pooled_pairs, pooled_hits_n = [], 0
    for source in ("roasters_kr", "shopify_gauged"):
        sub = [r for r in rows if r["source"] == source]
        n_total = len(sub)
        scored = [(r, *score_sweetness(r["text"], variant)) for r in sub]
        covered = [(r, lab, hits) for r, lab, hits in scored if lab is not None]
        pairs = [(r["sweetness"], lab) for r, lab, _ in covered]
        pooled_pairs += pairs
        pooled_hits_n += len(covered)
        mean_truth = sum(r["sweetness"] for r in sub) / n_total if n_total else None
        baseline_pairs = [(r["sweetness"], mean_truth) for r, lab, _ in covered]
        m = attr_metrics(pairs) if pairs else {"n": 0}
        b = attr_metrics(baseline_pairs) if baseline_pairs else {"n": 0}
        per_source[source] = {
            "n_total": n_total, "n_covered": len(covered),
            "coverage": round(len(covered) / n_total, 4) if n_total else None,
            "weak_label": m, "weak_label_recentered": attr_metrics(_recentered(pairs)) if pairs else {"n": 0},
            "constant_baseline": b,
        }
    n_total_all = len(rows)
    mean_truth_all = sum(r["sweetness"] for r in rows) / n_total_all if n_total_all else None
    baseline_pooled = [(t, mean_truth_all) for t, _ in pooled_pairs]
    pooled = {
        "n_total": n_total_all, "n_covered": pooled_hits_n,
        "coverage": round(pooled_hits_n / n_total_all, 4) if n_total_all else None,
        "weak_label": attr_metrics(pooled_pairs) if pooled_pairs else {"n": 0},
        "weak_label_recentered": attr_metrics(_recentered(pooled_pairs)) if pooled_pairs else {"n": 0},
        "constant_baseline": attr_metrics(baseline_pooled) if baseline_pooled else {"n": 0},
    }
    return {"per_source": per_source, "pooled": pooled}


def roast_convention(rows: list[dict], variant: str) -> dict:
    """Spearman of the weak label (and, for comparison, the gauge itself) against roast level ordinal, to see
    which sweetness convention (light-roast-fruit-sweet vs dark-roast-caramel-sweet, ADR 0020's addendum) the
    weak label follows."""
    pts_weak, pts_gauge = [], []
    for r in rows:
        ro = ROAST_ORDINAL.get(r["roast_level"])
        if ro is None:
            continue
        lab, _ = score_sweetness(r["text"], variant)
        pts_gauge.append((ro, r["sweetness"]))
        if lab is not None:
            pts_weak.append((ro, lab))
    return {
        "n_roast_known": len(pts_gauge),
        "gauge_vs_roast_spearman": spearman([a for a, _ in pts_gauge], [b for _, b in pts_gauge]) if pts_gauge else None,
        "n_weak_covered_roast_known": len(pts_weak),
        "weak_label_vs_roast_spearman": spearman([a for a, _ in pts_weak], [b for _, b in pts_weak]) if pts_weak else None,
    }


def scaleup_yield() -> dict:
    if not SCAN_PATH.exists():
        return {"error": f"scan file not found: {SCAN_PATH}"}
    scan = json.loads(SCAN_PATH.read_text(encoding="utf-8"))
    ok = [s for s in scan if s.get("status") == "ok"]
    products_p1 = sum(s.get("products", 0) for s in ok)
    sweet_lines_p1 = sum(s.get("raw_sweet_lines", 0) for s in ok)
    stores_with_any = sum(1 for s in ok if s.get("raw_sweet_lines", 0) > 0)
    return {
        "n_stores_scanned": len(scan), "n_stores_ok_robots_allowed": len(ok),
        "page1_products_total": products_p1, "page1_products_mentioning_sweet": sweet_lines_p1,
        "page1_sweet_mention_rate": round(sweet_lines_p1 / products_p1, 4) if products_p1 else None,
        "stores_with_any_sweet_mention": stores_with_any,
        "note": "page-1 counts only (no full catalog collected); raw_sweet_lines counts body_html lines "
                "containing 'sweet', a proxy for products with sweetness prose, not a dedup'd product count.",
    }


def _snippet(text: str, hits: list[str], width: int = 90) -> str:
    """A window of context around the first lexicon hit, collapsed to one line -- more useful than the first N
    chars, which for long prose (e.g. shopify_gauged) is often boilerplate before the sensory line ever matches."""
    one_line = re.sub(r"\s+", " ", text).strip()
    if not hits:
        return one_line[:width]
    low = one_line.lower()
    pos = low.find(hits[0].lower())
    if pos < 0:
        return one_line[:width]
    start = max(0, pos - width // 2)
    return one_line[start:start + width]


def examples(rows: list[dict], variant: str, k: int = 5) -> list[dict]:
    scored = []
    for r in rows:
        lab, hits = score_sweetness(r["text"], variant)
        if lab is not None:
            scored.append({
                "source": r["source"], "key": r["key"], "roaster": r["roaster"],
                "text_snippet": _snippet(r["text"], hits), "matched": hits[:6],
                "weak_label": round(lab, 2), "gauge_sweetness": r["sweetness"],
                "abs_diff": round(abs(lab - r["sweetness"]), 2),
            })
    scored.sort(key=lambda x: x["abs_diff"])
    picks = scored[:3] + scored[-2:] if len(scored) >= 5 else scored
    return picks


DECISION_SPEARMAN_MIN = 0.35
DECISION_COVERAGE_MIN = 0.40


def decide(pooled: dict) -> dict:
    # judged on the recentered label (ADR 0013's precedent: the lexicon only knows direction, not level, so the
    # gate uses the label re-centered onto the truth mean, not the raw one -- raw is still reported above).
    wl, bl = pooled["weak_label_recentered"], pooled["constant_baseline"]
    cov = pooled["coverage"] or 0.0
    sp = wl.get("spearman")
    beats_within1 = wl.get("within1", 0) > bl.get("within1", 1)
    beats_mae = wl.get("mae", 9) < bl.get("mae", 0)
    go = bool(sp is not None and sp >= DECISION_SPEARMAN_MIN and cov >= DECISION_COVERAGE_MIN
              and beats_within1 and beats_mae)
    return {"go": go, "coverage": cov, "spearman_recentered": sp, "spearman_raw": pooled["weak_label"].get("spearman"),
            "beats_baseline_within1": beats_within1, "beats_baseline_mae": beats_mae,
            "rule": f"spearman(recentered)>={DECISION_SPEARMAN_MIN} and coverage>={DECISION_COVERAGE_MIN}"
                    " and recentered label beats constant-mean baseline on both +-1 and MAE"}


def main():
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = load_rows(conn)

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_rows": len(rows),
        "n_by_source": {s: sum(1 for r in rows if r["source"] == s) for s in ("roasters_kr", "shopify_gauged")},
        "shopify_gauged_raw_prose_found": sum(1 for r in rows if r["source"] == "shopify_gauged" and r["has_raw_prose"]),
        "variants": {},
        "roast_convention": {},
        "scaleup_yield_estimate": scaleup_yield(),
        "examples_full_variant": examples(rows, "full"),
        "decision": {},
    }
    for variant in ("full", "notes_only"):
        m = eval_variant(rows, variant)
        result["variants"][variant] = m
        result["roast_convention"][variant] = roast_convention(rows, variant)
        if variant == "full":
            result["decision"] = decide(m["pooled"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(json.dumps({"pooled_full": result["variants"]["full"]["pooled"], "decision": result["decision"],
                       "roast_convention_full": result["roast_convention"]["full"],
                       "scaleup_yield_estimate": result["scaleup_yield_estimate"]},
                      ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
