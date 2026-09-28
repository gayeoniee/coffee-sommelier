"""Render the 5 result figures for the competition submission (spec doc section 5) from
eval JSON files.

Usage: uv run python scripts/competition/figures.py <eval_dir> <out_dir>

Reads (all under <eval_dir>):
  phase2_violations.json, phase2_coverage.json, phase2_compare3.json,
  phase2_convergence.json, phase2_bench.json, phase2_explain_quality.json (optional).

Writes 5 PNGs into <out_dir>:
  01_violations.png, 02_loo_compare.png, 03_decaf_coverage.png,
  04_convergence.png, 05_latency_quality.png.

Korean labels use the system "Malgun Gothic" font when available; otherwise labels fall
back to English so text never renders as tofu boxes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

BLUE = "#2f6fed"
ORANGE = "#e07a3f"
GRAY = "#9aa5b1"

PERSONA_LABELS_KO = ["디카페인+산미", "저카페인+우유X", "제한없음", "디카페인+우유X+단맛"]
PERSONA_LABELS_EN = {
    "디카페인+산미": "Decaf+Acidity",
    "저카페인+우유X": "LowCaf+NoMilk",
    "제한없음": "NoConstraint",
    "디카페인+우유X+단맛": "Decaf+NoMilk+Sweet",
}
BRAND_LABELS_EN = {
    "coffeebean": "CoffeeBean", "compose": "Compose", "hollys": "Hollys",
    "mega": "MegaMGC", "paik": "PaikDabang", "paulbassett": "PaulBassett",
    "starbucks": "Starbucks",
}
VARIANT_ORDER = ["full", "open", "open_plus"]
VARIANT_LABELS_KO = {"full": "전체", "open": "오픈", "open_plus": "오픈+로스터리"}
VARIANT_LABELS_EN = {"full": "Full", "open": "Open", "open_plus": "Open+KR"}

NOISE_FILE_CANDIDATES = (
    "phase2_loo.json", "phase2_loo_bge-m3.json",
    "phase2_loo_open.json", "phase2_loo_open_bge-m3.json",
)


def _korean_font_available() -> bool:
    try:
        names = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:
        return False
    return "Malgun Gothic" in names


def _configure_font(korean: bool) -> None:
    if korean:
        plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False


def _label(name: str, korean: bool, en_table: dict) -> str:
    return name if korean else en_table.get(name, name)


def _load(eval_dir: Path, name: str) -> dict:
    return json.loads((Path(eval_dir) / name).read_text(encoding="utf-8"))


def _load_optional(eval_dir: Path, name: str) -> dict | None:
    path = Path(eval_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_compare3(eval_dir: Path) -> dict:
    """phase2_compare3.json compares the full/open/open_plus variants side by side, so a
    single copy is generated once (in the parent eval dir shared across variant runs) rather
    than duplicated into every variant's own eval dir. Prefer a copy inside eval_dir itself
    (tests write one there) and fall back to the parent directory (real per-variant runs,
    e.g. data/eval/open, read the shared data/eval/phase2_compare3.json)."""
    eval_dir = Path(eval_dir)
    for candidate in (eval_dir / "phase2_compare3.json", eval_dir.parent / "phase2_compare3.json"):
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"phase2_compare3.json not found in {eval_dir} or {eval_dir.parent}")


def _fallback_brands(eval_dir: Path) -> list[str]:
    cov = _load_optional(eval_dir, "phase2_coverage.json")
    if cov and cov.get("menu_items_by_brand"):
        return sorted(k.split(":", 1)[-1] for k in cov["menu_items_by_brand"])
    return ["brand"]


# ---------------------------------------------------------------------------
# 01_violations.png
# ---------------------------------------------------------------------------

def fig_violations(eval_dir: Path, out_path: Path, korean: bool) -> None:
    data = _load(eval_dir, "phase2_violations.json")
    details = data.get("details", [])
    personas = sorted({d["persona"] for d in details}) or list(PERSONA_LABELS_KO)
    brands = sorted({d["brand"] for d in details}) or _fallback_brands(eval_dir)

    matrix = [[0] * len(brands) for _ in personas]
    for d in details:
        try:
            i, j = personas.index(d["persona"]), brands.index(d["brand"])
        except ValueError:
            continue
        matrix[i][j] += 1

    fig, ax = plt.subplots(figsize=(max(6.0, len(brands) * 1.1), max(3.0, len(personas) * 0.9)))
    vmax = max(1, max((max(row) for row in matrix), default=1))
    im = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(brands)))
    ax.set_xticklabels([_label(b, korean, BRAND_LABELS_EN) for b in brands], rotation=30, ha="right")
    ax.set_yticks(range(len(personas)))
    ax.set_yticklabels([_label(p, korean, PERSONA_LABELS_EN) for p in personas])
    for i, row in enumerate(matrix):
        for j, v in enumerate(row):
            ax.text(j, i, str(v), ha="center", va="center", color="white" if v else "#1f2933")

    checked, violations, rate = data.get("checked", 0), data.get("violations", 0), data.get("rate", 0.0)
    title = (f"조건 위반: {violations}/{checked}건 ({rate:.1%})" if korean
             else f"Violations: {violations}/{checked} ({rate:.1%})")
    ax.set_title(title)
    fig.colorbar(im, ax=ax, shrink=0.8, label=("위반 건수" if korean else "violations"))
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 02_loo_compare.png
# ---------------------------------------------------------------------------

def _noise_for(eval_dir: Path, exclude_sources: list[str]) -> dict[str, float] | None:
    """Half-range of within1 accuracy across alternate-embedding reruns of the SAME LOO
    configuration (same exclude_sources) — an approximate measurement-noise band."""
    target = sorted(exclude_sources or [])
    candidates = []
    for name in NOISE_FILE_CANDIDATES:
        d = _load_optional(eval_dir, name)
        if d is not None and sorted(d.get("exclude_sources", []) or []) == target:
            candidates.append(d)
    if len(candidates) < 2:
        return None
    noise: dict[str, float] = {}
    for attr in ("acidity", "body"):
        values = [c[attr]["within1"] for c in candidates
                  if c.get(attr, {}).get("within1") is not None]
        if len(values) >= 2:
            noise[attr] = (max(values) - min(values)) / 2
    return noise or None


def fig_loo_compare(eval_dir: Path, out_path: Path, korean: bool) -> None:
    """Acidity and body are scored on TWO DIFFERENT fixed target sets (docs/adr/0010-body-heaviness.md):
    acidity on the CQI-200 set (`loo.acidity`), body on a separate coffeereview-only set with a heaviness
    label (`body_loo.body`) -- CQI's own body is always None, so `loo.body` would be n=0 for every variant.
    Both n's are annotated on the bars since they differ per variant (the body pool shrinks a bit once
    coffeereview_kaggle itself is excluded from the neighbour pool)."""
    cmp = _load_compare3(eval_dir)
    variants = [v for v in VARIANT_ORDER if v in cmp.get("variants", {})]
    labels = VARIANT_LABELS_KO if korean else VARIANT_LABELS_EN

    acidity = [cmp["variants"][v]["loo"]["acidity"]["within1"] for v in variants]
    acidity_n = [cmp["variants"][v]["loo"]["acidity"]["n"] for v in variants]
    body = [cmp["variants"][v]["body_loo"]["body"]["within1"] for v in variants]
    body_n = [cmp["variants"][v]["body_loo"]["body"]["n"] for v in variants]
    acidity_err, body_err = [], []
    for v in variants:
        noise = _noise_for(eval_dir, cmp["variants"][v].get("exclude_sources", []))
        acidity_err.append((noise or {}).get("acidity", 0.0))
        body_err.append((noise or {}).get("body", 0.0))

    x = list(range(len(variants)))
    width = 0.35
    fig, ax = plt.subplots(figsize=(max(5.0, len(variants) * 2.2), 4.2))
    bars_a = ax.bar([i - width / 2 for i in x], acidity, width, yerr=acidity_err, capsize=4,
                    label=("산미 (CQI n)" if korean else "Acidity (CQI n)"), color=BLUE)
    bars_b = ax.bar([i + width / 2 for i in x], body, width, yerr=body_err, capsize=4,
                    label=("바디 (coffeereview n)" if korean else "Body (coffeereview n)"), color=ORANGE)
    for rect, n in zip(bars_a, acidity_n):
        ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height(), f"n={n}",
               ha="center", va="bottom", fontsize=8)
    for rect, n in zip(bars_b, body_n):
        ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height(), f"n={n}",
               ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([labels.get(v, v) for v in variants])
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("±1 이내 정확도" if korean else "±1 accuracy")
    title = ("LOO 산미·바디 ±1 정확도 (서로 다른 고정 대상)" if korean
             else "LOO acidity/body within-1 accuracy (separate fixed target sets)")
    ax.set_title(title)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 03_decaf_coverage.png
# ---------------------------------------------------------------------------

def _shorten(text: str, max_len: int) -> str:
    """Shorten a table label to at most max_len characters so matplotlib's table cell
    never clips it mid-word against the cell border. Cuts on a trailing space when one
    falls near the limit so words are not chopped in half."""
    text = text or ""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rstrip()
    space = cut.rfind(" ")
    if space >= max_len // 2:
        cut = cut[:space]
    return cut + "…"


def fig_decaf_coverage(eval_dir: Path, out_path: Path, korean: bool) -> None:
    cmp = _load_compare3(eval_dir)
    variants = [v for v in VARIANT_ORDER if v in cmp.get("variants", {})]
    labels = VARIANT_LABELS_KO if korean else VARIANT_LABELS_EN
    candidates = [cmp["variants"][v]["decaf_probe"]["candidates"] for v in variants]

    fig, (ax_bar, ax_table) = plt.subplots(1, 2, figsize=(12.5, 4), gridspec_kw={"width_ratios": [1, 1.9]})
    bars = ax_bar.bar([labels.get(v, v) for v in variants], candidates, color=BLUE)
    for rect, val in zip(bars, candidates):
        ax_bar.text(rect.get_x() + rect.get_width() / 2, val, str(val), ha="center", va="bottom")
    ax_bar.set_title("디카페인 후보 수" if korean else "Decaf candidates")
    ax_bar.set_ylabel("원두 수" if korean else "coffees")

    last_variant = variants[-1]
    top = (cmp["variants"][last_variant]["decaf_probe"].get("top") or [])[:5]
    ax_table.axis("off")
    col_labels = ["원두", "로스터리", "점수"] if korean else ["Coffee", "Roaster", "Score"]
    rows = [[_shorten(t.get("name", ""), 26), _shorten(t.get("roaster", ""), 18),
             f"{t.get('score', 0):.4f}"] for t in top]
    if rows:
        tbl = ax_table.table(cellText=rows, colLabels=col_labels, loc="center", cellLoc="left",
                              colWidths=[0.6, 0.28, 0.12])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1, 1.5)
    else:
        ax_table.text(0.5, 0.5, "no data", ha="center", va="center")
    ax_table.set_title(
        f"디카페인+산미 top5 ({last_variant})" if korean else f"Decaf+Acidity top5 ({last_variant})")

    fig.suptitle("디카페인 커버리지" if korean else "Decaf coverage")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 04_convergence.png
# ---------------------------------------------------------------------------

def fig_convergence(eval_dir: Path, out_path: Path, korean: bool) -> None:
    data = _load(eval_dir, "phase2_convergence.json")
    mae = data.get("mae_by_step") or []
    steps = list(range(len(mae)))

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(steps, mae, marker="o", color=BLUE)
    ax.set_xlabel("기록 횟수" if korean else "records")
    ax.set_ylabel("MAE")
    title = (f"모의 사용자 {data.get('users', '-')}명 수렴 (개선 {data.get('improvement', 0):.4f})" if korean
             else f"Convergence, {data.get('users', '-')} users (improved {data.get('improvement', 0):.4f})")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 05_latency_quality.png
# ---------------------------------------------------------------------------

def fig_latency_quality(eval_dir: Path, out_path: Path, korean: bool) -> None:
    bench = _load(eval_dir, "phase2_bench.json")
    eq = _load_optional(eval_dir, "phase2_explain_quality.json")

    fig, (ax_lat, ax_qual) = plt.subplots(1, 2, figsize=(11, 4))

    lat_labels = ["순차", "병렬"] if korean else ["Sequential", "Parallel"]
    totals = [bench.get("sequential_total_s", 0.0), bench.get("parallel_total_s", 0.0)]
    bars = ax_lat.bar(lat_labels, totals, color=[GRAY, BLUE])
    for rect, val in zip(bars, totals):
        ax_lat.text(rect.get_x() + rect.get_width() / 2, val, f"{val:.2f}s", ha="center", va="bottom")
    ax_lat.set_ylabel("초" if korean else "seconds")
    ax_lat.set_title(f"설명 생성 지연 ({bench.get('model', '-')})" if korean
                      else f"Explain latency ({bench.get('model', '-')})")

    if eq is not None:
        summary = eq.get("summary", {})
        metrics = ["rule_pass_rate", "no_contradiction_rate_both", "no_hallucination_rate_both"]
        qual_labels = ["규칙 통과", "무모순", "무환각"] if korean else \
            ["Rule pass", "No contradiction", "No hallucination"]
        values = [summary.get(m) or 0.0 for m in metrics]
        bars2 = ax_qual.bar(qual_labels, values, color=BLUE)
        for rect, val in zip(bars2, values):
            ax_qual.text(rect.get_x() + rect.get_width() / 2, val, f"{val:.0%}", ha="center", va="bottom")
        ax_qual.set_ylim(0, 1)
        ax_qual.set_title("설명 품질 판정" if korean else "Explain quality")
    else:
        ax_qual.axis("off")
        ax_qual.text(0.5, 0.5, "설명 품질 데이터 없음" if korean else "no explain-quality data",
                     ha="center", va="center")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------

FIGURES = [
    ("01_violations.png", fig_violations),
    ("02_loo_compare.png", fig_loo_compare),
    ("03_decaf_coverage.png", fig_decaf_coverage),
    ("04_convergence.png", fig_convergence),
    ("05_latency_quality.png", fig_latency_quality),
]


def main(eval_dir: Path, out_dir: Path) -> list[Path]:
    eval_dir, out_dir = Path(eval_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    korean = _korean_font_available()
    _configure_font(korean)
    paths = []
    for filename, render in FIGURES:
        out_path = out_dir / filename
        render(eval_dir, out_path, korean)
        paths.append(out_path)
    return paths


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: figures.py <eval_dir> <out_dir>", file=sys.stderr)
        sys.exit(2)
    for p in main(Path(sys.argv[1]), Path(sys.argv[2])):
        print(p)
