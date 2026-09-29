"""External check: score the product's bean predictor against an independent Q-grader panel (Zenodo).

Data: "Electrochemical profiles of coffee drink samples" v1.1, Golovinsky R., Ivanov I., Aglikov A. et al.,
Zenodo 2026, https://doi.org/10.5281/zenodo.20840464 (file panelists_scores_EN.xlsx: 526 rows, 196 samples,
1~4 SCA Q graders per sample). The record's metadata says CC BY 4.0 but its description says CC BY-NC 4.0, so we
follow the stricter one (BY-NC): the file is downloaded to data/raw/ (gitignored), used ONLY here as an external
evaluation set, never for training, never loaded into any DB, never redistributed. Only aggregate metrics are
written (data/eval/phase2_zenodo_external.json). docs/adr/0014-public-sources-ediya-twosome-kca-zenodo.md.

Per sample:
  truth    acidity/sweetness: panel mean of the 5-level INTENSITY column (low 1 · below middle 2 · middle 3 ·
           above middle 4 · high 5); body: the free-text body description through BODY_WORDS (a small keyword
           map, documented below), panel mean over panelists whose text had a weight word.
  input    a bean-card-like text, built the way a guest would paste one: producer + origin (Russian country
           names translated by COUNTRY_RU, process words by PROCESS_RU) + roast (ROAST_FIELD) + "decaf" when the
           name says so + the panel's aroma/bouquet notes. The acidity/sweetness/bitterness/body description
           columns are NOT in the text -- they state the answers ("below average", "high").
  predict  the real analyze path's core functions (app/graphs/analyze_bean.py `predict`), rules-only parse (no
           LLM parse), k=10 neighbours with the same origin/process filter:
             full : coffee DB neighbours → learned tag model → learned attribute model → text cues
             open : coffee_open DB neighbours (thin tag vote topped up from tagged-only neighbours, ADR 0017)
                    → roaster-gauge feature model (its shipped heads) → text cues
                    (no attribute/tag model ships for open; an attribute without a head = neighbour average)
             open_neighbours : the open variant with the feature model off (neighbour averages + text cues)
  tags     flavor-tag agreement vs the panel's aroma/bouquet/aftertaste words through the SAME rule mapper the
           pipeline uses (pipeline.enrich.rule_tags). Scored on the tags BEFORE text cues: the final tags echo
           the input text's own note words (with_text_cues), which would be circular.

Embeddings are cached in data/raw/zenodo_panel/query_embeddings.jsonl (keyed by model + text hash).

Usage:
    uv run python scripts/eval_zenodo_panel.py            # downloads the xlsx once (md5-checked)
    FULL_DATABASE_URL=... OPEN_DATABASE_URL=... uv run python scripts/eval_zenodo_panel.py
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import settings  # noqa: E402

RECORD_URL = "https://zenodo.org/records/20840464/files/panelists_scores_EN.xlsx?download=1"
RECORD_MD5 = "a0fddda083c2726511f7dc3a29e3bef0"
DATA_DIR = settings.RAW_DIR / "zenodo_panel"
XLSX = DATA_DIR / "panelists_scores_EN.xlsx"
EMB_CACHE = DATA_DIR / "query_embeddings.jsonl"
OUT = settings.EVAL_DIR / "phase2_zenodo_external.json"
ATTRIBUTION = ("Golovinsky R., Ivanov I., Aglikov A., Aliev T., Timofeenko I., Novikov A., Zayats M., Ashikhmina M., "
               "Orlova O., Masalovich M., Skorb E. (2026). Electrochemical profiles of coffee drink samples (v1.1) "
               "[Data set]. Zenodo. https://doi.org/10.5281/zenodo.20840464 — licence CC BY-NC 4.0 (stricter of "
               "the record's two statements); used unmodified, evaluation only.")

INTENSITY = {"low": 1, "below middle": 2, "middle": 3, "above middle": 4, "high": 5}
# Body: weight words only (texture words -- smooth, silky, rough, dry, tart -- say nothing about weight).
# Two-word phrases are matched first so "below average" is not also read as "average".
BODY_PHRASES = {"below average": 2, "above average": 4, "below medium": 2, "above medium": 4}
BODY_WORDS = {"watery": 1, "empty": 1, "thin": 1.5, "light": 2, "tea": 2, "tea-like": 2, "delicate": 2, "low": 2,
              "medium": 3, "average": 3, "round": 3, "rounded": 3, "dense": 4, "density": 4, "enveloping": 4,
              "oily": 4, "creamy": 4, "high": 4, "syrupy": 4.5, "thick": 4.5, "full": 4.5, "heavy": 5}
COUNTRY_RU = {"Бразилия": "Brazil", "Эфиопия": "Ethiopia", "Колумбия": "Colombia", "Китай": "China",
              "Коста- Рика": "Costa Rica", "Коста -Рика": "Costa Rica", "Коста-Рика": "Costa Rica",
              "Боливия": "Bolivia", "Кения": "Kenya", "Руанда": "Rwanda", "Перу": "Peru", "Ява": "Java",
              "Индонезия": "Indonesia", "Гватемала": "Guatemala", "Суматра": "Sumatra", "Уганда": "Uganda",
              "Сальвадор": "El Salvador", "Папуа": "Papua New Guinea", "Гондурас": "Honduras",
              "Вьетнам": "Vietnam", "Бурунди": "Burundi", "колумбия": "Colombia", "Смесь": "blend",
              "смесь": "blend", "робуста": "robusta", "Академия Кофе": "Akademia Coffee",
              "Чёрная дыра": "Black Hole", "Концепция кофе": "Coffee Concept", "Жокей": "Jockey"}
PROCESS_RU = {r"\bНат\b": "natural", r"мытая": "washed", r"из бочки": "barrel aged"}
DECAF_RE = re.compile(r"декаф|dekaf|decaf", re.I)
# The sheet's roast field is a brew-purpose label, not a colour; mapped to the catalogue scale by assumption.
ROAST_FIELD = [(re.compile(r"filter\s*/\s*espresso", re.I), "medium"), (re.compile(r"filter", re.I), "light"),
               (re.compile(r"milk drinks", re.I), "dark"), (re.compile(r"espresso", re.I), "medium"),
               (re.compile(r"\b(light|medium|dark) roast", re.I), None)]
COLS = {"id": 0, "panelist": 1, "name": 2, "roast": 3, "aroma": 4, "bouquet": 6, "aftertaste": 8,
        "acidity_int": 11, "sweetness_int": 14, "body": 19}


# ---- xlsx (stdlib only; the project has no openpyxl) ---------------------------------------------------
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_xlsx_rows(path: Path, sheet: int = 1) -> list[list]:
    """Rows of one worksheet as lists (shared strings resolved, numbers as float, empty cells None)."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    rows = []
    for r in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, object] = {}
        for c in r.findall("m:c", _NS):
            ref = re.match(r"([A-Z]+)", c.get("r")).group(1)
            col = 0
            for ch in ref:
                col = col * 26 + ord(ch) - 64
            v = c.find("m:v", _NS)
            if c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None:
                val = None
            elif c.get("t") == "s":
                val = shared[int(v.text)]
            elif c.get("t") == "str":
                val = v.text
            else:
                val = float(v.text)
            cells[col - 1] = val
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


# ---- truth and input text ---------------------------------------------------------------------------------
def body_value(text: str | None) -> float | None:
    """Mean of the weight words in one panelist's body description (None if it names no weight)."""
    t = (text or "").lower()
    vals = []
    for phrase, v in BODY_PHRASES.items():
        if phrase in t:
            vals.append(v)
            t = t.replace(phrase, " ")
    vals += [BODY_WORDS[w] for w in re.findall(r"[a-z][a-z-]*", t) if w in BODY_WORDS]
    return sum(vals) / len(vals) if vals else None


def roast_word(field: str | None) -> str | None:
    for pat, level in ROAST_FIELD:
        m = pat.search(field or "")
        if m:
            return level or m.group(1).lower()
    return None


def english_name(name: str | None) -> str:
    n = name or ""
    for ru, en in sorted(COUNTRY_RU.items(), key=lambda kv: -len(kv[0])):
        n = n.replace(ru, en)
    for pat, en in PROCESS_RU.items():
        n = re.sub(pat, en, n)
    return " ".join(n.replace("–", " ").replace("-", " ").split())


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def samples_from_rows(rows: list[list]) -> list[dict]:
    """One dict per sample: panel means (truth), the bean-card text (input), and the panel's note words."""
    by: dict[int, list[list]] = {}
    for r in rows[2:]:                      # two header rows
        if not r or r[0] is None:
            continue
        r = r + [None] * (len(COLS) + 12 - len(r))
        by.setdefault(int(r[COLS["id"]]), []).append(r)
    out = []
    for sid, rs in sorted(by.items()):
        name = next((r[COLS["name"]] for r in rs if r[COLS["name"]]), None)
        roast_field = next((r[COLS["roast"]] for r in rs if r[COLS["roast"]]), None)
        truth = {"acidity": _mean([INTENSITY[r[COLS["acidity_int"]]] for r in rs
                                   if r[COLS["acidity_int"]] in INTENSITY]),
                 "sweetness": _mean([INTENSITY[r[COLS["sweetness_int"]]] for r in rs
                                     if r[COLS["sweetness_int"]] in INTENSITY]),
                 "body": _mean([v for r in rs if (v := body_value(r[COLS["body"]])) is not None])}
        notes = list(dict.fromkeys(w.strip() for r in rs for c in ("aroma", "bouquet")
                                   for w in str(r[COLS[c]] or "").split(",") if w.strip()))
        tag_text = ", ".join(str(r[COLS[c]]) for r in rs for c in ("aroma", "bouquet", "aftertaste") if r[COLS[c]])
        decaf = bool(DECAF_RE.search(name or ""))
        roast = roast_word(roast_field)
        parts = [english_name(name), f"{roast} roast" if roast else "", "decaf" if decaf else "",
                 "notes: " + ", ".join(notes) if notes else ""]
        out.append({"id": sid, "panelists": len(rs), "is_decaf": decaf, "roast": roast, "truth": truth,
                    "text": " | ".join(p for p in parts if p), "tag_text": tag_text})
    return out


# ---- metrics ---------------------------------------------------------------------------------------------
def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 4) if den else None


def attr_metrics(pairs: list[tuple[float, float]]) -> dict:
    """pairs = (truth, prediction)."""
    if not pairs:
        return {"n": 0}
    t, p = [a for a, _ in pairs], [b for _, b in pairs]
    return {"n": len(pairs), "mae": round(sum(abs(a - b) for a, b in pairs) / len(pairs), 4),
            "within1": round(sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs), 4),
            "spearman": spearman(t, p), "mean_truth": round(sum(t) / len(t), 3), "mean_pred": round(sum(p) / len(p), 3)}


def tag_metrics(rows: list[tuple[set, set]], tag_to_cat: dict[str, str]) -> dict:
    tp = fp = fn = ctp = cfp = cfn = 0
    for truth, pred in rows:
        tp, fp, fn = tp + len(truth & pred), fp + len(pred - truth), fn + len(truth - pred)
        tc, pc = {tag_to_cat[t] for t in truth if t in tag_to_cat}, {tag_to_cat[t] for t in pred if t in tag_to_cat}
        ctp, cfp, cfn = ctp + len(tc & pc), cfp + len(pc - tc), cfn + len(tc - pc)

    def f1(a, b, c):
        p, r = (a / (a + b) if a + b else 0.0), (a / (a + c) if a + c else 0.0)
        return round(2 * p * r / (p + r), 4) if p + r else 0.0, round(p, 4), round(r, 4)

    f, p, r = f1(tp, fp, fn)
    return {"n": len(rows), "precision": p, "recall": r, "f1": f, "category_f1": f1(ctp, cfp, cfn)[0]}


# ---- prediction (the analyze path's core functions) -----------------------------------------------------------
class CachedEmbedder:
    def __init__(self, path: Path = EMB_CACHE):
        from pipeline.llm import embed_model, embedder_for
        self.model, self.path, self.inner, self.requests = embed_model(), path, None, 0
        self.cache: dict[str, list[float]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["key"]] = row["vector"]
        self._factory = embedder_for

    def __call__(self, text: str) -> list[float]:
        key = hashlib.sha1(f"{self.model}\n{text}".encode()).hexdigest()
        if key not in self.cache:
            self.inner = self.inner or self._factory()
            vec = self.inner.embed_query(text)
            self.requests += 1
            self.cache[key] = vec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "vector": vec}) + "\n")
        return self.cache[key]


def predict(sample: dict, vec, repo, tax, attr_model=None, tag_model=None, feature_model=None, tag_fill=False,
            tag_cooc=None):
    """Mirror of app/graphs/analyze_bean.py `predict` (rules-only parse; embedding succeeded). Returns the final
    Prediction and the tags BEFORE text cues."""
    from app.core.featuremodel import with_feature_model
    from app.core.parse import parse_bean_text
    from app.core.predict import (
        FILL_EXCLUDE_SOURCES, FILL_MIN_TAGS, predict_from_neighbors, with_model_attrs, with_model_tags, with_tag_fill,
        with_text_cues,
    )

    tag_to_cat, tag_ko, base_rates = tax
    parsed = parse_bean_text(sample["text"])
    neighbors = repo.neighbors(vec, 10, parsed.origin_country, parsed.process)
    pred = predict_from_neighbors(neighbors, tag_ko, base_rates=base_rates)
    if tag_fill and len(pred.tags) < FILL_MIN_TAGS:
        tagged = repo.neighbors(vec, 10, parsed.origin_country, parsed.process, exclude_sources=FILL_EXCLUDE_SOURCES,
                                tagged_only=True)
        pred = with_tag_fill(pred, tagged, tag_ko, base_rates=base_rates)
    if tag_model is not None:
        pred = with_model_tags(pred, tag_model.tags(vec), tag_ko)
    if attr_model is not None:
        pred = with_model_attrs(pred, attr_model.predict(vec))
    if feature_model is not None:
        pred = with_feature_model(pred, feature_model, parsed, tag_to_cat, tag_ko)
    tags_before_cues = set(pred.tags)
    pred = with_text_cues(pred, parsed.text, tag_ko=tag_ko, tag_to_cat=tag_to_cat)
    if tag_cooc is not None:        # open variant: co-occurrence top-up (docs/adr/0018-open-tag-cooccurrence.md)
        from app.core.tagcooc import with_cooc_fill
        from app.core.textcues import text_tags
        pred = with_cooc_fill(pred, text_tags(parsed.text, tag_to_cat, tag_ko, free_text=True), tag_cooc, tag_ko)
    return pred, tags_before_cues


def download(path: Path = XLSX) -> Path:
    import httpx
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(RECORD_URL, follow_redirects=True, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
    md5 = hashlib.md5(path.read_bytes()).hexdigest()
    if md5 != RECORD_MD5:
        raise SystemExit(f"{path} md5 {md5} != Zenodo's {RECORD_MD5}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", type=Path, default=XLSX)
    ap.add_argument("--full-db", default=os.getenv("FULL_DATABASE_URL", "postgresql://coffee:coffee@localhost:5432/coffee"))
    ap.add_argument("--open-db", default=os.getenv("OPEN_DATABASE_URL",
                                                   "postgresql://coffee:coffee@localhost:5432/coffee_open"))
    args = ap.parse_args()

    from app.core.attrmodel import AttrModel
    from app.core.featuremodel import FeatureModel
    from app.core.tagcooc import TagCooc
    from app.core.tagmodel import TagModel
    from app.models import ATTRS
    from app.repo import Repo
    from pipeline.enrich import rule_tags

    path = download(args.xlsx) if args.xlsx == XLSX else args.xlsx
    samples = samples_from_rows(read_xlsx_rows(path))
    embed = CachedEmbedder()
    variants = {
        "full": {"db": args.full_db, "attr_model": AttrModel.load(settings.CONFIG_DIR / "attr_model.json"),
                 "tag_model": TagModel.load(settings.CONFIG_DIR / "tag_model.json"), "feature_model": None,
                 "tag_fill": False, "tag_cooc": None},
        "open": {"db": args.open_db, "attr_model": None, "tag_model": None,
                 "feature_model": FeatureModel.load(settings.CONFIG_DIR / "feature_model_open.json"), "tag_fill": True,
                 "tag_cooc": TagCooc.load(settings.CONFIG_DIR / "tag_cooc_open.json")},
        # the open variant with the feature model off (FEATURE_MODEL=off): neighbour averages + text cues -- the
        # baseline every feature-model head has to beat on this external panel too
        "open_neighbours": {"db": args.open_db, "attr_model": None, "tag_model": None, "feature_model": None,
                            "tag_fill": True, "tag_cooc": TagCooc.load(settings.CONFIG_DIR / "tag_cooc_open.json")},
    }
    result = {"source": "https://doi.org/10.5281/zenodo.20840464", "file": "panelists_scores_EN.xlsx",
              "file_md5": RECORD_MD5, "attribution": ATTRIBUTION,
              "use": "external evaluation only -- never training, never loaded into a DB, not redistributed",
              "samples": len(samples), "rows": sum(s["panelists"] for s in samples),
              "decaf_samples": sum(s["is_decaf"] for s in samples),
              "mapping": {"intensity": INTENSITY, "body_phrases": BODY_PHRASES, "body_words": BODY_WORDS,
                          "roast_field": {"filter": "light", "espresso": "medium", "filter/espresso": "medium",
                                          "milk drinks": "dark"}},
              "embedding_model": embed.model,
              "model_files_sha1": {f: hashlib.sha1((settings.CONFIG_DIR / f).read_bytes()).hexdigest()[:12]
                                   for f in ("attr_model.json", "tag_model.json", "feature_model_open.json",
                                             "tag_cooc_open.json")
                                   if (settings.CONFIG_DIR / f).exists()},
              "variants": {}}
    pairs_const = {a: [] for a in ATTRS}
    by_sample: dict[str, dict[tuple[int, str], float]] = {}     # variant -> (sample id, attr) -> prediction
    for name, v in variants.items():
        repo = Repo(v["db"])
        try:
            tag_to_cat, tag_ko = repo.taxonomy()
            tax = (tag_to_cat, tag_ko, repo.tag_base_rates())
            vocab = list(tag_to_cat)
            pairs = {a: [] for a in ATTRS}
            n_truth = {a: 0 for a in ATTRS}      # an abstained prediction (None, ADR 0016) lowers coverage
            tag_rows = []
            parsed_origin = 0
            for s in samples:
                pred, tags_before = predict(s, embed(s["text"]), repo, tax, v["attr_model"], v["tag_model"],
                                            v["feature_model"], v["tag_fill"], v["tag_cooc"])
                from app.core.parse import parse_bean_text
                parsed_origin += parse_bean_text(s["text"]).origin_country is not None
                for a in ATTRS:
                    t, p = s["truth"][a], getattr(pred, a)
                    n_truth[a] += t is not None
                    if t is not None and p is not None:
                        pairs[a].append((t, p))
                        by_sample.setdefault(name, {})[(s["id"], a)] = p
                        if name == "full":
                            pairs_const[a].append((t, 3.0))
                truth_tags = set(rule_tags(s["tag_text"], vocab, limit=12))
                if truth_tags:
                    tag_rows.append((truth_tags, {t.lower() for t in tags_before}))
            result["variants"][name] = {
                "predictor": {"full": "coffee DB neighbours → tag model → attribute model → text cues",
                              "open": "coffee_open DB neighbours → roaster-gauge feature model → text cues",
                              "open_neighbours": "coffee_open DB neighbours → text cues (feature model off)"}[name],
                "parsed_origin": parsed_origin,
                **{a: {**attr_metrics(pairs[a]), "coverage": round(len(pairs[a]) / n_truth[a], 4) if n_truth[a] else None}
                   for a in ATTRS},
                "tags_before_text_cues": tag_metrics(tag_rows, tag_to_cat)}
        finally:
            repo.close()
    result["baseline_constant_3"] = {a: attr_metrics(p) for a, p in pairs_const.items()}
    # open feature model vs the open neighbour average vs always-3 on the SAME samples (those the neighbour
    # average covers) -- the head-to-head a shipped feature-model head must win on this panel too
    truth = {(s["id"], a): s["truth"][a] for s in samples for a in ATTRS}
    paired = {}
    for a in ("acidity", "sweetness"):
        keys = [k for k in by_sample["open_neighbours"] if k[1] == a and k in by_sample["open"]]
        paired[a] = {"open_feature_model": attr_metrics([(truth[k], by_sample["open"][k]) for k in keys]),
                     "open_neighbour_average": attr_metrics([(truth[k], by_sample["open_neighbours"][k]) for k in keys]),
                     "constant_3": attr_metrics([(truth[k], 3.0) for k in keys])}
    result["open_paired"] = paired
    result["embedding_requests"] = embed.requests
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, r in result["variants"].items():
        print(name, {a: (r[a].get("n"), r[a].get("mae"), r[a].get("within1"), r[a].get("spearman")) for a in ATTRS},
              r["tags_before_text_cues"])
    print("const3", {a: (r.get("mae"), r.get("within1")) for a, r in result["baseline_constant_3"].items()})
    for a, r in paired.items():
        print("paired", a, {k: (m["n"], m["mae"], m["within1"], m["spearman"]) for k, m in r.items()})
    print(f"wrote {OUT} ({embed.requests} embedding requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
