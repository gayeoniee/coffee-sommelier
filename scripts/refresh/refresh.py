"""Automated data refresh: collect -> diff -> enrich/embed (only new/changed beans) -> gates -> publish.
One entry point for local runs and the weekly GitHub Actions job (.github/workflows/refresh.yml).
Design, gates and failure modes: docs/adr/0015-automated-refresh.md.

    DRY_RUN=1 uv run python -m scripts.refresh.refresh            # everything except the production writes/PR
    uv run python -m scripts.refresh.refresh --raw-dir data/refresh/raw --skip-pytest

Databases (env; every URL stays out of logs and the report):
    REFRESH_SOURCE_URL     the catalog as it is now            (default DATABASE_URL, i.e. the local dev DB)
    REFRESH_STAGE_URL      scratch DB, dropped and rebuilt     (default .../coffee_refresh on the same server)
    REFRESH_STAGE_OPEN_URL scratch open-data DB                (default .../coffee_refresh_open)
    PUBLISH_FULL_URL       where a passing refresh is written  (default REFRESH_SOURCE_URL)
    PUBLISH_OPEN_URL       open-data production DB             (default: none -> the open publish is skipped)
Other env: DRY_RUN=1, ENRICH_TASK (enrich_ci in CI: no Ollama there), REFRESH_MAX_LLM_CALLS (default 300).

Stages (each records its status in data/refresh/<date>/report.{json,md}; one failing source never stops the others):
  collect   brand menus + shopify + shopify_gauged + roasters-kr (+ the SCA wheel), robots.txt-checked and delayed
            by pipeline.http.PoliteClient; a source already collected in the last 6 days is not fetched again
  normalize the live sources only (the static datasets -- coffeereview, CQI, RoasterDB -- are never re-collected)
  stage     the current catalog is copied into REFRESH_STAGE_URL (scripts/refresh/catalog_sync.py)
  diff      new/removed/changed menus and beans vs that copy; caffeine changes > 20 % flagged; new names with no
            hand milk label listed as "라벨 필요" (loaded with needs_review, out of recommendations until labelled)
  enrich    only new/changed beans (LLM), everything else is reused from the DB as is
  embed     only new/changed beans (NVIDIA)
  load      scoped load into the staging DB (pipeline/load.py coffee_scope/brand_scope); open-data staging DB
            derived from it with the build_open_db.sh rules
  gates     size check (<= 20 % of a table removed), violations 0 (both variants), LOO acidity within +-1 >=
            committed baseline - 0.02 (both variants), README numbers re-generated and checked, pytest
  publish   only when every gate passed AND something changed AND not DRY_RUN: catalog_sync staging -> production
            (full and open; catalog tables only, users/tastings never touched), then a PR `refresh/<date>` with the
            regenerated eval JSON + README/draft numbers and this report, auto-merge (squash) once CI passes.
            A failed gate writes nothing to a DB and opens (or comments on) the issue "데이터 갱신 실패 <date>".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

from pipeline import settings
from pipeline.load import coffee_group, load_milk_labels, run_load
from pipeline.records import (
    BrandRecord,
    CoffeeRecord,
    MenuItemRecord,
    ReviewRecord,
    TaxonomyNode,
    read_jsonl,
    write_jsonl,
)
from scripts.refresh import catalog_sync, gates
from scripts.refresh.diff import diff_coffees, diff_menus, menu_unchanged, size_check
from scripts.refresh.report import render_markdown

STATIC_SOURCES = ("coffeereview_kaggle", "cqi", "roasterdb", "mfds_food")  # fixed datasets, never re-collected
# mfds_food (docs/adr/0023-mfds-food-db.md) is a manually re-downloaded government dataset, not a live scrape:
# the weekly refresh must never retire its menu items just because data/raw/mfds_food/*.xlsx (gitignored) is
# absent in the refresh environment. Updating it is a manual `data/raw/mfds_food/` download + a full
# `pipeline run --only normalize --only load` (not this script) — see the ADR's update procedure.
LIVE_COFFEE_SOURCES = ("roasters_kr", "shopify", "shopify_gauged")
MENU_SOURCES = ("starbucks", "mega", "paik", "coffeebean", "compose", "hollys", "paulbassett", "ediya")
COLLECT_SOURCES = MENU_SOURCES + ("shopify", "shopify_gauged", "sca_wheel")
MAX_AGE_DAYS = 6              # a snapshot this fresh is reused (weekly job re-run / actions cache): polite crawling
ROOT = settings.ROOT
COFFEE_COLS = ("key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level", "is_decaf",
               "decaf_process", "acidity", "body", "sweetness", "flavor_tags", "flavor_summary", "altitude_m",
               "variety", "attr_label_source", "source", "source_url", "collected_at")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def with_db(url: str, db: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + db, p.query, p.fragment))


def where(url: str | None) -> str | None:
    """A URL as the report shows it: local/remote + database name, never credentials or hosts."""
    if not url:
        return None
    p = urlsplit(url)
    host = "local" if p.hostname in ("localhost", "127.0.0.1", "db") else "remote"
    return f"{host}:{p.path.lstrip('/')}"


@dataclass
class Config:
    date: str
    dry_run: bool
    work: Path
    raw_dir: Path
    source_url: str
    stage_url: str
    stage_open_url: str
    publish_full_url: str | None
    publish_open_url: str | None
    skip_collect: bool = False
    skip_pytest: bool = False
    open_pr: bool = True
    max_llm_calls: int = 300
    sources: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, a) -> "Config":
        date = a.date or dt.date.today().isoformat()
        source = os.getenv("REFRESH_SOURCE_URL") or settings.DATABASE_URL
        local = settings.DATABASE_URL
        return cls(date=date, dry_run=a.dry_run or _env_flag("DRY_RUN"),
                   work=Path(a.work_dir) if a.work_dir else settings.DATA_DIR / "refresh" / date,
                   raw_dir=Path(a.raw_dir) if a.raw_dir else settings.RAW_DIR,
                   source_url=source,
                   stage_url=os.getenv("REFRESH_STAGE_URL") or with_db(local, "coffee_refresh"),
                   stage_open_url=os.getenv("REFRESH_STAGE_OPEN_URL") or with_db(local, "coffee_refresh_open"),
                   publish_full_url=os.getenv("PUBLISH_FULL_URL") or source,
                   publish_open_url=os.getenv("PUBLISH_OPEN_URL") or None,
                   skip_collect=a.skip_collect, skip_pytest=a.skip_pytest, open_pr=not a.no_pr,
                   max_llm_calls=int(os.getenv("REFRESH_MAX_LLM_CALLS") or a.max_llm_calls),
                   sources=tuple(a.source or ()))


@dataclass
class Report:
    date: str
    dry_run: bool
    stages: dict = field(default_factory=dict)
    gates: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    @property
    def gates_ok(self) -> bool:
        return bool(self.gates) and all(g["ok"] for g in self.gates) and not self.errors

    def to_dict(self) -> dict:
        return {"date": self.date, "dry_run": self.dry_run, "ok": self.gates_ok,
                "elapsed_s": round(time.time() - self.started, 1), "stages": self.stages, "gates": self.gates,
                "errors": self.errors}


# ---------------------------------------------------------------- collect / normalize
def _fresh(snapshot_dir: Path | None, today: dt.date) -> bool:
    if snapshot_dir is None:
        return False
    try:
        return (today - dt.date.fromisoformat(snapshot_dir.name)).days <= MAX_AGE_DAYS
    except ValueError:
        return False


def collect(cfg: Config) -> dict[str, str]:
    from pipeline.collect import latest_snapshot, run_collect
    from pipeline.collect.registry import ALL_COLLECTORS
    from pipeline.collect.roasters_kr import run_roasters_kr_collect
    from pipeline.http import PoliteClient

    today = dt.date.fromisoformat(cfg.date)
    wanted = [s for s in COLLECT_SOURCES + ("roasters_kr",) if not cfg.sources or s in cfg.sources]
    status: dict[str, str] = {}
    by_name = {c.name: c for c in ALL_COLLECTORS}
    with PoliteClient(delay=1.0) as http:
        for name in wanted:
            if name == "roasters_kr":
                continue
            if _fresh(latest_snapshot(cfg.raw_dir, name), today):
                status[name] = "cached"
                continue
            (m,) = run_collect([by_name[name]], cfg.raw_dir, http, cfg.date)
            status[name] = "ok" if m.ok else f"failed: {m.error}"[:300]
    if "roasters_kr" in wanted:
        beans = cfg.raw_dir / "roasters_kr" / "beans.jsonl"
        if beans.exists() and (time.time() - beans.stat().st_mtime) < MAX_AGE_DAYS * 86400:
            status["roasters_kr"] = "cached"
        else:
            with PoliteClient(delay=2.0) as http:
                try:
                    stats, _ = run_roasters_kr_collect(http, cfg.raw_dir, cfg.date)
                    bad = sorted(k for k, v in stats.items() if isinstance(v, dict) and v.get("error"))
                    status["roasters_kr"] = "ok" if not bad else f"partial: {', '.join(bad)} failed"
                except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
                    status["roasters_kr"] = f"failed: {type(e).__name__}: {e}"[:300]
    return status


def normalize(cfg: Config, stage) -> dict:
    from pipeline.normalize import run_normalize
    out = cfg.work / "normalized"
    counts = run_normalize(cfg.raw_dir, out, settings.CURATED_DIR, exclude_sources=STATIC_SOURCES)
    if not read_jsonl(out / "taxonomy.jsonl", TaxonomyNode):            # SCA wheel not collected: use the DB's
        rows = stage.execute("SELECT t.key, p.key, t.level, t.name_en, t.name_ko FROM flavor_taxonomy t"
                             " LEFT JOIN flavor_taxonomy p ON p.id = t.parent_id").fetchall()
        write_jsonl(out / "taxonomy.jsonl", [TaxonomyNode(key=k, parent_key=pk, level=lv, name_en=en, name_ko=ko)
                                             for k, pk, lv, en, ko in rows])
        counts["taxonomy_from_db"] = len(rows)
    return counts


# ---------------------------------------------------------------- databases
def recreate_db(url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    assert name and name.replace("_", "").isalnum() and "refresh" in name, f"refusing to drop {name!r}"
    with psycopg.connect(with_db(url, "postgres"), autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")


def clone(src_url: str, dst_url: str, variant: str = "full") -> dict:
    from pipeline.db import apply_schema
    recreate_db(dst_url)
    with psycopg.connect(src_url) as src, psycopg.connect(dst_url) as dst:
        apply_schema(dst)
        return catalog_sync.sync(src, dst, variant=variant)


def current_menus(conn) -> tuple[dict[str, dict], set[str]]:
    rows = conn.execute("SELECT m.key, b.key, m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,"
                        " m.caffeine_mg, m.source_url, m.collected_at FROM menu_items m JOIN brands b"
                        " ON b.id = m.brand_id WHERE m.active").fetchall()
    menus = {r[0]: {"brand_key": r[1], "name": r[2], "name_en": r[3], "category": r[4], "is_decaf": r[5],
                    "decaf_option": r[6], "caffeine_mg": r[7], "source_url": r[8], "collected_at": r[9]}
             for r in rows}
    brands = {k for (k,) in conn.execute("SELECT key FROM brands WHERE active").fetchall()}
    return menus, brands


def current_coffees(conn, sources=LIVE_COFFEE_SOURCES) -> tuple[dict[str, dict], dict[str, str], dict[str, dict]]:
    """(coffees by key incl. "embedding" as a list, review text per coffee key, reviews by key)."""
    cols = ", ".join(f"c.{c}" for c in COFFEE_COLS)
    rows = conn.execute(f"SELECT {cols}, c.embedding::text FROM coffees c WHERE c.active AND c.source = ANY(%s)",
                        (list(sources),)).fetchall()
    coffees = {}
    for r in rows:
        d = dict(zip(COFFEE_COLS, r[:-1]))
        d["embedding"] = json.loads(r[-1]) if r[-1] else None
        coffees[d["key"]] = d
    rev = conn.execute("SELECT r.key, c.key, r.text, r.collected_at FROM reviews r JOIN coffees c ON c.id = r.coffee_id"
                       " WHERE c.source = ANY(%s) ORDER BY r.id", (list(sources),)).fetchall()
    texts: dict[str, list[str]] = {}
    reviews = {}
    for rk, ck, text, at in rev:
        texts.setdefault(ck, []).append(text)
        reviews[rk] = {"text": text, "collected_at": at}
    return coffees, {k: "\n".join(sorted(v)) for k, v in texts.items()}, reviews


def _record_from_db(d: dict) -> CoffeeRecord:
    return CoffeeRecord(**{k: (v.isoformat() if k == "collected_at" else v) for k, v in d.items()
                           if k in COFFEE_COLS})


# ---------------------------------------------------------------- enrich / embed (new or changed beans only)
def enrich_and_embed(cfg: Config, todo: set[str], norm: Path, new_coffees: list[CoffeeRecord],
                     new_reviews: list[ReviewRecord]) -> tuple[list[CoffeeRecord], dict[str, list[float]], dict]:
    from pipeline.embed import embedding_text
    from pipeline.enrich import coffee_texts, read_json_lines, run_enrich
    from pipeline.llm import client_for, embed_model, embedder_for, enrich_task

    stats: dict = {"todo": len(todo)}
    if not todo:
        return [], {}, stats
    src = cfg.work / "enrich_in"
    out = cfg.work / "enriched"
    write_jsonl(src / "coffees.jsonl", [c for c in new_coffees if c.key in todo])
    write_jsonl(src / "reviews.jsonl", [r for r in new_reviews if r.coffee_key in todo])
    shutil.copyfile(norm / "taxonomy.jsonl", src / "taxonomy.jsonl")
    out.mkdir(parents=True, exist_ok=True)
    local_cache = settings.ENRICHED_DIR / "cache.jsonl"                     # a local run reuses earlier LLM answers
    if local_cache.exists() and not (out / "cache.jsonl").exists():
        rows = [e for e in read_json_lines(local_cache)[0] if e["key"] in todo]
        (out / "cache.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rows),
                                         encoding="utf-8")
    task = enrich_task()
    stats["enrich"] = run_enrich(src, out, client_for(task), limit=cfg.max_llm_calls)
    stats["enrich_task"] = task
    enriched = read_jsonl(out / "coffees.jsonl", CoffeeRecord)

    import hashlib
    model = embed_model()
    texts = coffee_texts(new_reviews)
    local_emb = {e["key"]: e for e in read_json_lines(settings.embedded_dir(model) / "embeddings.jsonl")[0]}
    vectors, need = {}, []
    for c in enriched:
        text = embedding_text(c, texts.get(c.key))
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if c.key in local_emb and local_emb[c.key]["hash"] == h:
            vectors[c.key] = local_emb[c.key]["vector"]
        else:
            need.append((c.key, text))
    embedder = embedder_for() if need else None
    if need:
        for (key, _), v in zip(need, embedder.embed([t for _, t in need])):
            vectors[key] = v
    stats["embed"] = {"model": model, "embedded": len(need), "reused_local_cache": len(enriched) - len(need),
                      "requests": embedder.requests if embedder else 0}
    return enriched, vectors, stats


# ---------------------------------------------------------------- the run
def _preserve_collected_at(records, old: dict[str, dict], same) -> list:
    out = []
    for r in records:
        o = old.get(r.key)
        if o is not None and same(o, r) and o.get("collected_at"):
            r = r.model_copy(update={"collected_at": o["collected_at"].isoformat()
                                     if hasattr(o["collected_at"], "isoformat") else o["collected_at"]})
        out.append(r)
    return out


def build_and_stage(cfg: Config, rep: Report, stage) -> dict:
    """normalize -> diff -> enrich/embed -> scoped load into the staging DB. Returns what the gates need."""
    norm = cfg.work / "normalized"
    rep.stages["normalize"] = normalize(cfg, stage)
    labels = load_milk_labels()
    new_menus = read_jsonl(norm / "menu_items.jsonl", MenuItemRecord)
    new_coffees = [c for c in read_jsonl(norm / "coffees.jsonl", CoffeeRecord) if c.source in LIVE_COFFEE_SOURCES]
    new_reviews = [r for r in read_jsonl(norm / "reviews.jsonl", ReviewRecord)
                   if r.coffee_key in {c.key for c in new_coffees}]
    old_menus, old_brands = current_menus(stage)
    old_coffees, old_review_text, old_reviews = current_coffees(stage)
    brand_scope = {m.brand_key for m in new_menus}
    new_review_text: dict[str, list[str]] = {}
    for r in new_reviews:
        new_review_text.setdefault(r.coffee_key, []).append(r.text)
    mdiff = diff_menus(old_menus, new_menus, labels, brand_scope, old_brands)
    cdiff = diff_coffees(old_coffees, new_coffees, old_review_text,
                         {k: "\n".join(sorted(v)) for k, v in new_review_text.items()})
    todo = set(cdiff.pop("todo"))
    scope = set(cdiff["scope"])
    rep.stages["diff"] = {"menus": mdiff, "coffees": cdiff}

    total_coffees = stage.execute("SELECT count(*) FROM coffees WHERE active").fetchone()[0]
    old_group = Counter(coffee_group(o["source"], o.get("roaster")) for o in old_coffees.values())
    new_group = Counter(coffee_group(c.source, c.roaster) for c in new_coffees)
    old_brand_n = Counter(o["brand_key"] for o in old_menus.values())
    new_brand_n = Counter(m.brand_key for m in new_menus)
    fails = size_check(
        {"coffees": (total_coffees, cdiff["counts"]["removed"]), "menu_items": (len(old_menus), mdiff["counts"]["removed"])},
        [(g, old_group[g], new_group[g]) for g in sorted(scope)] + [(b, old_brand_n[b], new_brand_n[b]) for b in sorted(brand_scope)])

    enriched, vectors, es = enrich_and_embed(cfg, todo, norm, new_coffees, new_reviews)
    rep.stages["enrich_embed"] = es

    # the load input: unchanged beans exactly as the DB has them, new/changed ones freshly enriched
    load_dir = cfg.work / "load"
    by_key = {c.key: c for c in enriched}
    final = [by_key[c.key] if c.key in todo else _record_from_db(old_coffees[c.key]) for c in new_coffees
             if c.key in by_key or c.key not in todo]
    vec = {**{k: old_coffees[k]["embedding"] for k in (c.key for c in final) if k not in todo}, **vectors}
    missing = [c.key for c in final if vec.get(c.key) is None]
    if missing:
        raise RuntimeError(f"임베딩 없는 원두 {len(missing)}개: {missing[:5]}")
    write_jsonl(load_dir / "enriched" / "coffees.jsonl", final)
    (load_dir / "embedded").mkdir(parents=True, exist_ok=True)
    with (load_dir / "embedded" / "embeddings.jsonl").open("w", encoding="utf-8") as f:
        for c in final:
            f.write(json.dumps({"key": c.key, "hash": "refresh", "vector": vec[c.key]}) + "\n")
    ln = load_dir / "norm"
    write_jsonl(ln / "brands.jsonl", read_jsonl(norm / "brands.jsonl", BrandRecord))
    shutil.copyfile(norm / "taxonomy.jsonl", ln / "taxonomy.jsonl")
    write_jsonl(ln / "menu_items.jsonl", _preserve_collected_at(new_menus, old_menus, menu_unchanged))
    write_jsonl(ln / "reviews.jsonl", _preserve_collected_at(new_reviews, old_reviews, lambda o, r: o["text"] == r.text))
    rep.stages["stage_load"] = run_load(stage, ln, load_dir / "enriched", load_dir / "embedded",
                                        coffee_scope=scope, brand_scope=brand_scope, milk_labels=labels)
    return {"size_fails": fails, "has_diff": any(
        mdiff["counts"][k] for k in ("new", "removed", "caffeine_changed", "decaf_changed")) or any(
        cdiff["counts"][k] for k in ("new", "removed", "changed"))}


def numbers_gate(cfg: Config, full_eval: Path, open_eval: Path) -> tuple[gates.Gate, Path]:
    """Overlay the new eval JSON on a copy of data/eval, regenerate README numbers there, and check them."""
    from scripts.check_readme_numbers import HEADLINE_CHECKS, check, find_skipped
    from scripts.refresh.update_numbers import update_draft, update_readme

    d = cfg.work / "numbers"
    ev = d / "eval"
    if d.exists():
        shutil.rmtree(d)
    (ev / "open").mkdir(parents=True)
    for p in (settings.DATA_DIR / "eval").glob("*.json"):
        shutil.copyfile(p, ev / p.name)
    for p in (settings.DATA_DIR / "eval" / "open").glob("*.json"):
        shutil.copyfile(p, ev / "open" / p.name)
    for p in full_eval.glob("*.json"):
        shutil.copyfile(p, ev / p.name)
    for p in open_eval.glob("*.json"):
        shutil.copyfile(p, ev / "open" / p.name)
    readme, changed = update_readme((ROOT / "README.md").read_text(encoding="utf-8"), ev)
    (d / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    draft = update_draft((ROOT / "docs/competition/data-recipe-draft.md").read_text(encoding="utf-8"), ev / "open")
    (d / "data-recipe-draft.md").write_text(draft, encoding="utf-8", newline="\n")
    errors = check(readme, ev)
    headline = [s for s in find_skipped(readme) if s in HEADLINE_CHECKS]
    ok = not errors and not headline
    return gates.Gate("readme_numbers", ok, f"README 수치 {changed}개 갱신, 불일치 {len(errors)}"
                      + (f": {errors[:3]}" if errors else "") + (f", 누락 {headline}" if headline else "")), d


def run_gates(cfg: Config, rep: Report, staged: dict) -> Path | None:
    rep.gates.append(gates.check_size(staged["size_fails"]).to_dict())
    full_eval, open_eval = cfg.work / "eval" / "full", cfg.work / "eval" / "open"
    for variant, url, out in (("full", cfg.stage_url, full_eval), ("open", cfg.stage_open_url, open_eval)):
        code, log = gates.run_evals(url, out, variant)
        (cfg.work / f"eval_{variant}.log").write_text(log, encoding="utf-8")
        if code != 0:
            rep.gates.append(gates.check_command(f"eval_{variant}", code, log).to_dict())
            continue
        rep.gates.append(gates.check_violations(variant, gates.load_json(out / "phase2_violations.json") or {}).to_dict())
        base = settings.DATA_DIR / "eval" / ("phase2_loo.json" if variant == "full" else "open/phase2_loo.json")
        rep.gates.append(gates.check_loo(variant, gates.load_json(out / "phase2_loo.json") or {},
                                         gates.load_json(base)).to_dict())
    numbers_dir = None
    if (full_eval / "phase2_coverage.json").exists() and (open_eval / "phase2_coverage.json").exists():
        g, numbers_dir = numbers_gate(cfg, full_eval, open_eval)
        rep.gates.append(g.to_dict())
    if cfg.skip_pytest:
        rep.gates.append(gates.Gate("pytest", True, "건너뜀(--skip-pytest)").to_dict())
    else:
        code, log = gates.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
        rep.gates.append(gates.check_command("pytest", code, log).to_dict())
    return numbers_dir


def publish_plan(cfg: Config, rep: Report, write: bool) -> None:
    from pipeline.db import apply_schema
    out = {}
    for label, src_url, dst_url in (("full", cfg.stage_url, cfg.publish_full_url),
                                    ("open", cfg.stage_open_url, cfg.publish_open_url)):
        if not dst_url:
            out[label] = {"target": None, "skipped": "대상 URL 없음"}
            continue
        with psycopg.connect(src_url) as src, psycopg.connect(dst_url) as dst:
            if write:
                apply_schema(dst)
            plan = catalog_sync.sync(src, dst, dry_run=not write)
        out[label] = {"target": where(dst_url), "written": write, "plan": plan}
    rep.stages["publish"] = out


def git_pr(cfg: Config, rep: Report, numbers_dir: Path, report_md: Path) -> dict:
    """Branch refresh/<date> with the regenerated eval JSON and numbers, PR with the report, auto-merge."""
    branch = f"refresh/{cfg.date}"
    full_eval, open_eval = cfg.work / "eval" / "full", cfg.work / "eval" / "open"
    steps = []

    def sh(*cmd: str) -> str:
        code, log = gates.run(list(cmd))
        steps.append({"cmd": " ".join(cmd[:3]), "code": code})
        if code != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} 실패: {log[-400:]}")
        return log

    sh("git", "switch", "-c", branch)
    for p in full_eval.glob("phase2_*.json"):
        shutil.copyfile(p, settings.DATA_DIR / "eval" / p.name)
    for p in open_eval.glob("phase2_*.json"):
        shutil.copyfile(p, settings.DATA_DIR / "eval" / "open" / p.name)
    shutil.copyfile(numbers_dir / "README.md", ROOT / "README.md")
    shutil.copyfile(numbers_dir / "data-recipe-draft.md", ROOT / "docs/competition/data-recipe-draft.md")
    sh("git", "add", "data/eval", "README.md", "docs/competition/data-recipe-draft.md")
    sh("git", "commit", "-m", f"data: 주간 자동 갱신 {cfg.date} — 평가 수치 재생성")
    sh("git", "push", "-u", "origin", branch)
    body = report_md.read_text(encoding="utf-8")[:60000]
    body_file = cfg.work / "pr_body.md"
    body_file.write_text(body, encoding="utf-8")
    url = sh("gh", "pr", "create", "--title", f"데이터 자동 갱신 {cfg.date}", "--body-file", str(body_file),
             "--base", "main", "--head", branch).strip().splitlines()[-1]
    # The gates above already ran the checks CI would run; auto-merge waits for any required ones. Non-fatal: the DBs
    # are published either way, and an unmerged PR only leaves the committed numbers a week behind.
    code, log = gates.run(["gh", "pr", "merge", "--auto", "--squash", branch])
    steps.append({"cmd": "gh pr merge", "code": code})
    return {"branch": branch, "pr": url, "auto_merge": "ok" if code == 0 else log.strip()[-300:], "steps": steps}


def failure_issue(cfg: Config, report_md: Path) -> dict:
    title = f"데이터 갱신 실패 {cfg.date}"
    body = report_md.read_text(encoding="utf-8")[:60000]
    body_file = cfg.work / "issue_body.md"
    body_file.write_text(body, encoding="utf-8")
    code, log = gates.run(["gh", "issue", "list", "--state", "open", "--search", f"in:title {title}",
                           "--json", "number,title"])
    existing = [i for i in (json.loads(log) if code == 0 and log.strip().startswith("[") else []) if i["title"] == title]
    if existing:
        code, log = gates.run(["gh", "issue", "comment", str(existing[0]["number"]), "--body-file", str(body_file)])
        return {"issue": existing[0]["number"], "action": "comment", "code": code}
    code, log = gates.run(["gh", "issue", "create", "--title", title, "--body-file", str(body_file)])
    return {"issue": log.strip().splitlines()[-1] if log.strip() else None, "action": "create", "code": code}


def write_report(cfg: Config, rep: Report) -> Path:
    cfg.work.mkdir(parents=True, exist_ok=True)
    d = rep.to_dict()
    (cfg.work / "report.json").write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = cfg.work / "report.md"
    md.write_text(render_markdown(d), encoding="utf-8")
    return md


def refresh(cfg: Config) -> Report:
    rep = Report(cfg.date, cfg.dry_run)
    rep.stages["config"] = {"source": where(cfg.source_url), "stage": where(cfg.stage_url),
                            "stage_open": where(cfg.stage_open_url), "publish_full": where(cfg.publish_full_url),
                            "publish_open": where(cfg.publish_open_url), "raw_dir": str(cfg.raw_dir),
                            "enrich_task": os.getenv("ENRICH_TASK") or "enrich"}
    cfg.work.mkdir(parents=True, exist_ok=True)
    try:
        rep.stages["collect"] = {"skipped": True} if cfg.skip_collect else collect(cfg)
        rep.stages["stage_clone"] = clone(cfg.source_url, cfg.stage_url)
        with psycopg.connect(cfg.stage_url) as stage:
            staged = build_and_stage(cfg, rep, stage)
        rep.stages["stage_open_build"] = clone(cfg.stage_url, cfg.stage_open_url, variant="open")
        numbers_dir = run_gates(cfg, rep, staged)
        rep.stages["has_diff"] = staged["has_diff"]
        publish = rep.gates_ok and not cfg.dry_run
        publish_plan(cfg, rep, write=publish)
        planned = any(v.get("plan") and any(t["new"] or t["changed"] or t["delete"] or t["retire"]
                                            for t in v["plan"].values()) for v in rep.stages["publish"].values())
        rep.stages["has_diff"] = staged["has_diff"] or planned
        md = write_report(cfg, rep)
        if publish and rep.stages["has_diff"] and cfg.open_pr and numbers_dir is not None:
            rep.stages["pr"] = git_pr(cfg, rep, numbers_dir, md)
        elif cfg.dry_run:
            rep.stages["pr"] = {"skipped": "DRY_RUN — PR·운영 DB 쓰기 없음"}
    except Exception as e:  # noqa: BLE001 - every failure ends in a report (and an issue)
        rep.errors.append(f"{type(e).__name__}: {e}")
        (cfg.work / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
    md = write_report(cfg, rep)
    if not rep.gates_ok:
        rep.stages["issue"] = ({"skipped": "DRY_RUN"} if cfg.dry_run or not cfg.open_pr
                               else failure_issue(cfg, md))
        md = write_report(cfg, rep)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="same as DRY_RUN=1")
    ap.add_argument("--date", help="snapshot/report date (default today)")
    ap.add_argument("--work-dir", help="default data/refresh/<date>")
    ap.add_argument("--raw-dir", help="raw snapshot dir (default data/raw; CI caches it per week)")
    ap.add_argument("--source", action="append", help="collect only these sources (repeatable)")
    ap.add_argument("--skip-collect", action="store_true", help="use the raw snapshots already on disk")
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--no-pr", action="store_true", help="publish DBs but open no PR/issue")
    ap.add_argument("--max-llm-calls", type=int, default=300)
    a = ap.parse_args(argv)
    cfg = Config.from_env(a)
    rep = refresh(cfg)
    print((cfg.work / "report.md").read_text(encoding="utf-8"))
    print(f"report: {cfg.work / 'report.json'}")
    return 0 if rep.gates_ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
