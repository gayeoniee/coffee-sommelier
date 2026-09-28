"""Monthly model retrain candidate (docs/adr/0015-automated-refresh.md): retrain the learned models on the current
catalog into a scratch directory, compare their held-out metrics with the committed ones, and open a PR only when
the candidate is better. Model changes are never merged automatically -- a person reviews the PR.

    uv run python -m scripts.refresh.retrain [--dry-run] [--work-dir data/refresh/retrain-<date>]

Models and the metric compared (same held-out targets as the shipped numbers):
  tag      scripts/train_tag_model.py --pin-holdout --out-dir     holdout.model.f1          (data/eval/phase2_tag_model.json)
  attr     scripts/train_attr_model.py --variant full --pin-holdout --out-dir
                                                                  attrs.<a>.holdout.model.within1 (phase2_attr_model.json)
  feature  scripts/train_feature_model.py --no-ship, DATA_DIR = scratch copy
                                                                  cv.<a>.table.ridge.within1 (open/phase3_feature_model.json)
"Better" = some metric up by >= MIN_GAIN and none down by more than MAX_DROP. Needs DATABASE_URL (full catalog with
reviews) and, for the feature model, the local coffee/coffee_open DBs train_feature_model.py reads plus
data/raw/roasters_kr/beans.jsonl; a model whose inputs are missing is reported as skipped, not failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

from pipeline import settings
from scripts.refresh import gates

MIN_GAIN = 0.01
MAX_DROP = 0.005
ATTRS = ("acidity", "body", "sweetness")
EVAL = settings.DATA_DIR / "eval"


def _get(d: dict | None, *path):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def metrics(model: str, doc: dict | None) -> dict[str, float | None]:
    if model == "tag":
        return {"f1": _get(doc, "holdout", "model", "f1")}
    if model == "attr":
        return {a: _get(doc, "attrs", a, "holdout", "model", "within1") for a in ATTRS}
    return {a: _get(doc, "cv", a, "table", "ridge", "within1") for a in ATTRS}


def compare(shipped: dict[str, float | None], candidate: dict[str, float | None]) -> dict:
    deltas = {k: round(candidate[k] - shipped[k], 4) for k in shipped
              if shipped.get(k) is not None and candidate.get(k) is not None}
    improved = bool(deltas) and any(v >= MIN_GAIN for v in deltas.values()) and all(v >= -MAX_DROP for v in deltas.values())
    return {"shipped": shipped, "candidate": candidate, "delta": deltas, "improved": improved}


def train(work: Path) -> dict:
    out: dict = {}
    py = sys.executable
    runs = {
        "tag": ([py, "scripts/train_tag_model.py", "--pin-holdout", "--out-dir", str(work / "tag")],
                work / "tag" / "phase2_tag_model.json", EVAL / "phase2_tag_model.json"),
        "attr": ([py, "scripts/train_attr_model.py", "--variant", "full", "--pin-holdout", "--out-dir", str(work / "attr")],
                 work / "attr" / "phase2_attr_model.json", EVAL / "phase2_attr_model.json"),
    }
    beans = settings.RAW_DIR / "roasters_kr" / "beans.jsonl"
    feature_data = work / "feature_data"
    if beans.exists():
        (feature_data / "raw" / "roasters_kr").mkdir(parents=True, exist_ok=True)
        (feature_data / "eval" / "open").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(beans, feature_data / "raw" / "roasters_kr" / "beans.jsonl")
        runs["feature"] = ([py, "scripts/train_feature_model.py", "--no-ship"],
                           feature_data / "eval" / "open" / "phase3_feature_model.json",
                           EVAL / "open" / "phase3_feature_model.json")
    else:
        out["feature"] = {"skipped": f"{beans} 없음"}
    for name, (cmd, cand_path, shipped_path) in runs.items():
        env = {"DATA_DIR": str(feature_data)} if name == "feature" else None
        code, log = gates.run(cmd, env=env, timeout=5400)
        (work / f"{name}.log").write_text(log, encoding="utf-8")
        if code != 0 or not cand_path.exists():
            out[name] = {"error": f"exit {code}: {log.strip().splitlines()[-1] if log.strip() else ''}"[:300]}
            continue
        out[name] = compare(metrics(name, gates.load_json(shipped_path)), metrics(name, gates.load_json(cand_path)))
        out[name]["candidate_dir"] = str(cand_path.parent)
    return out


def open_pr(date: str, result: dict, work: Path) -> dict:
    """Copy the improved models' config + eval JSON in, PR on retrain/<date> -- never auto-merged."""
    steps = []

    def sh(*cmd):
        code, log = gates.run(list(cmd))
        steps.append({"cmd": " ".join(cmd[:3]), "code": code})
        if code != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} 실패: {log[-300:]}")
        return log

    branch = f"retrain/{date}"
    sh("git", "switch", "-c", branch)
    paths = []
    if _get(result, "tag", "improved"):
        for p in (work / "tag").glob("tag_model.json*"):
            shutil.copyfile(p, settings.CONFIG_DIR / p.name)
            paths.append(f"config/{p.name}")
        shutil.copyfile(work / "tag" / "phase2_tag_model.json", EVAL / "phase2_tag_model.json")
        paths.append("data/eval/phase2_tag_model.json")
    if _get(result, "attr", "improved"):
        for p in (work / "attr").glob("attr_model.json*"):
            shutil.copyfile(p, settings.CONFIG_DIR / p.name)
            paths.append(f"config/{p.name}")
        shutil.copyfile(work / "attr" / "phase2_attr_model.json", EVAL / "phase2_attr_model.json")
        paths.append("data/eval/phase2_attr_model.json")
    if _get(result, "feature", "improved"):   # the canonical config is written by the script itself (deterministic)
        code, log = gates.run([sys.executable, "scripts/train_feature_model.py"], timeout=5400)
        if code != 0:
            raise RuntimeError(f"feature model ship failed: {log[-300:]}")
        paths += ["config/feature_model_open.json", "data/eval/open/phase3_feature_model.json"]
    sh("git", "add", *paths)
    sh("git", "commit", "-m", f"model: 월간 재학습 후보 {date} — 보류 지표 개선")
    sh("git", "push", "-u", "origin", branch)
    body = work / "pr_body.md"
    body.write_text(render(date, result) + "\n\n자동 병합하지 않는다 — 사람이 검토 후 병합(ADR 0015).\n", encoding="utf-8")
    url = sh("gh", "pr", "create", "--title", f"모델 재학습 후보 {date}", "--body-file", str(body), "--base", "main",
             "--head", branch).strip().splitlines()[-1]
    return {"branch": branch, "pr": url, "steps": steps}


def render(date: str, result: dict) -> str:
    lines = [f"# 월간 재학습 비교 {date}", "", "| 모델 | 지표 | 운영 | 후보 | 차이 |", "|---|---|---|---|---|"]
    for name, r in result.items():
        if not isinstance(r, dict) or "shipped" not in r:
            lines.append(f"| {name} | - | - | - | {r.get('skipped') or r.get('error') if isinstance(r, dict) else r} |")
            continue
        for k in r["shipped"]:
            lines.append(f"| {name} | {k} | {r['shipped'][k]} | {r['candidate'].get(k)} | {r['delta'].get(k)} |")
    lines += ["", "개선된 모델: " + (", ".join(n for n, r in result.items() if isinstance(r, dict) and r.get("improved"))
                                     or "없음 — PR 없음")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--work-dir")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args(argv)
    work = Path(a.work_dir) if a.work_dir else settings.DATA_DIR / "refresh" / f"retrain-{a.date}"
    work.mkdir(parents=True, exist_ok=True)
    result = train(work)
    improved = any(isinstance(r, dict) and r.get("improved") for r in result.values())
    if improved and not a.dry_run:
        result["pr"] = open_pr(a.date, result, work)
    (work / "retrain.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render(a.date, {k: v for k, v in result.items() if k != "pr"})
    (work / "retrain.md").write_text(md, encoding="utf-8")
    print(md)
    return 0 if all("error" not in r for r in result.values() if isinstance(r, dict)) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
