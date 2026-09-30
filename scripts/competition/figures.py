"""Render the result figures for the competition submission (docs/competition/data-recipe-draft.md B-6).

Usage: uv run python scripts/competition/figures.py <eval_dir> <out_dir> [<csv_dir>]

  <eval_dir>  e.g. data/eval/open   (phase2_violations.json, phase2_loo.json; phase2_compare3.json and
              phase2_zenodo_external.json are read from <eval_dir> or its parent)
  <csv_dir>   the portal CSVs (default data/competition: 04_데이터셋_menu_items.csv, 05_데이터셋_brands.csv)

Writes 4 PNGs into <out_dir>:
  01_caffeine_strip.png   caffeine per menu, by brand, with the 300 mg (pregnancy daily) and 30 mg (decaf) lines
  02_filter_before_after.png  score-mixed conditions (earlier logic) vs filter-first (now)
  03_decaf_coverage.png   decaf beans purchasable in Korea: open vs open + Korean roasteries, and the top 5
  04_external_validation.png  Zenodo external panel (acidity) vs baselines, and confidence calibration

The 5th image (05_product_card.png) is a screenshot of the live submission site
(web/scripts/card-shot.mjs), not a chart.

Korean labels use "Malgun Gothic" when available; otherwise labels fall back to English so text never
renders as tofu boxes.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

BLUE = "#2f6fed"      # our method
ORANGE = "#e07a3f"    # highlighted problem cases
GRAY = "#9aa5b1"      # context / baselines
INK = "#1f2933"
MUTED = "#52606d"

DECAF_MAX_MG = 30         # app/core/scoring.py
PREGNANCY_DAILY_MG = 300  # MFDS daily maximum for pregnant women

# Documented measurement of the earlier score-mixed logic (commit 333c522, README "조건 위반" paragraph):
# 3 of 78 picks broke the no-milk condition. Kept as a constant because that logic no longer exists in code.
BEFORE = {"violations": 3, "checked": 78, "examples": "마키아또 · 콘 파나 · 플랫 화이트 (우유 불가 손님에게)",
          "examples_en": "macchiato, con panna, flat white (to a no-milk guest)"}

DOMESTIC_SOURCES = {"roasters_kr", "shopify"}  # DB names: shopify = Blue Bottle Korea (bluebottle_kr in the CSV)
KCA_CREDIT = ("메뉴 카페인: 브랜드 공식 공시값(8곳, 2026-09 수집) · 출처: 식품의약품안전처 식품영양성분 데이터베이스"
              "(음식 DB, 2026-08-28 — 투썸·더벤티 등 8곳)")


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
    for side in ("top", "right"):
        plt.rcParams[f"axes.spines.{side}"] = False
    plt.rcParams["axes.edgecolor"] = GRAY
    plt.rcParams["axes.titleweight"] = "bold"


def _load(path_dir: Path, name: str) -> dict:
    return json.loads((Path(path_dir) / name).read_text(encoding="utf-8"))


def _load_here_or_parent(eval_dir: Path, name: str) -> dict:
    """Cross-variant files (compare3, zenodo) live once in data/eval/, shared by data/eval/open/ runs."""
    for candidate in (Path(eval_dir) / name, Path(eval_dir).parent / name):
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"{name} not found in {eval_dir} or its parent")


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _shorten(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _save(fig, out_path: Path) -> None:
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 01 caffeine strip plot
# ---------------------------------------------------------------------------

def fig_caffeine_strip(csv_dir: Path, out_path: Path, korean: bool) -> dict:
    menu = _read_csv(Path(csv_dir) / "04_데이터셋_menu_items.csv")
    brand_names = {b["key"]: b["name"] for b in _read_csv(Path(csv_dir) / "05_데이터셋_brands.csv")}
    rows = [r for r in menu if r["caffeine_mg"]]
    by_brand: dict[str, list[dict]] = {}
    for r in rows:
        by_brand.setdefault(r["brand_key"], []).append(r)
    brands = sorted(by_brand, key=lambda b: -max(float(r["caffeine_mg"]) for r in by_brand[b]))

    rng = random.Random(0)
    fig, ax = plt.subplots(figsize=(13, 5.6))
    over_300 = decaf_over = 0
    decaf_worst = None
    for x, b in enumerate(brands):
        for r in by_brand[b]:
            mg = float(r["caffeine_mg"])
            decaf_named = r["is_decaf"] == "true" or "디카페인" in r["name"]
            bad_decaf = decaf_named and mg > DECAF_MAX_MG
            over_300 += mg > PREGNANCY_DAILY_MG
            decaf_over += bad_decaf
            jitter = rng.uniform(-0.28, 0.28)
            r["_x"] = x + jitter
            if bad_decaf and (decaf_worst is None or mg > float(decaf_worst["caffeine_mg"])):
                decaf_worst = r
            if bad_decaf:
                ax.scatter(x + jitter, mg, s=46, color=ORANGE, edgecolor="white", linewidth=0.8, zorder=3)
            elif decaf_named:
                ax.scatter(x + jitter, mg, s=18, color=BLUE, alpha=0.8, linewidth=0, zorder=2)
            else:
                ax.scatter(x + jitter, mg, s=18, color=GRAY, alpha=0.55, linewidth=0, zorder=2)

    ax.axhline(PREGNANCY_DAILY_MG, color=INK, lw=1, ls="--", zorder=1)
    ax.axhline(DECAF_MAX_MG, color=ORANGE, lw=1, ls="--", zorder=1)
    right = len(brands) - 0.35
    ax.text(right, PREGNANCY_DAILY_MG,
            ("300mg 임산부 하루 권고 상한\n한 잔으로 넘는 메뉴 %d종" % over_300) if korean
            else "300 mg pregnancy daily max\n%d drinks exceed it in one cup" % over_300,
            ha="left", va="center", fontsize=9, color=INK)
    ax.text(right, DECAF_MAX_MG + 8,
            ("30mg 디카페인 상한(앱 규칙)\n'디카페인'인데 넘는 메뉴 %d종" % decaf_over) if korean
            else "30 mg decaf cap (app rule)\n%d 'decaf' drinks exceed it" % decaf_over,
            ha="left", va="bottom", fontsize=9, color=ORANGE)
    if decaf_worst is not None:
        mg = float(decaf_worst["caffeine_mg"])
        ax.annotate(f"{decaf_worst['name']} {mg:g}mg", (decaf_worst["_x"], mg),
                    xytext=(decaf_worst["_x"] - 0.2, mg + 75), fontsize=9, color=INK, ha="right",
                    arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.8})
    ax.set_xticks(range(len(brands)))
    ax.set_xticklabels([brand_names.get(b, b) if korean else b.split(":")[-1] for b in brands],
                        rotation=40, ha="right", fontsize=9)
    ax.set_xlim(-0.6, len(brands) - 0.4)
    ax.set_ylabel("1잔 카페인 (mg)" if korean else "caffeine per cup (mg)")
    ax.set_ylim(0, None)
    ax.grid(axis="y", color="#e4e7eb", lw=0.6)
    ax.set_axisbelow(True)
    handles = [
        plt.Line2D([], [], marker="o", ls="", color=GRAY, label="일반 메뉴" if korean else "regular"),
        plt.Line2D([], [], marker="o", ls="", color=BLUE, label="디카페인 ≤30mg" if korean else "decaf <= 30 mg"),
        plt.Line2D([], [], marker="o", ls="", color=ORANGE,
                   label="'디카페인'인데 30mg 초과" if korean else "'decaf' but > 30 mg"),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=False, fontsize=9)
    ax.set_title(("같은 커피도 한 잔 카페인이 브랜드·메뉴마다 크게 다르다 (메뉴 %d종)" % len(rows)) if korean
                 else "Caffeine per cup varies widely (%d drinks)" % len(rows), loc="left")
    fig.text(0.01, -0.09, KCA_CREDIT if korean else "Caffeine: each brand's published values (collected 2026-09)",
             fontsize=8, color=MUTED)
    _save(fig, out_path)
    return {"drinks": len(rows), "over_300": over_300, "decaf_over_30": decaf_over}


# ---------------------------------------------------------------------------
# 02 filter vs score, before/after
# ---------------------------------------------------------------------------

def fig_filter_before_after(eval_dir: Path, out_path: Path, korean: bool) -> None:
    now = _load(eval_dir, "phase2_violations.json")
    rows = [
        ("조건을 점수에 섞은 이전 로직" if korean else "earlier: conditions mixed into the score",
         BEFORE["violations"], BEFORE["checked"], ORANGE,
         BEFORE["examples"] if korean else BEFORE["examples_en"]),
        ("조건을 먼저 거르는 지금 (필터 → 점수)" if korean else "now: filter first, then score",
         now["violations"], now["checked"], BLUE, "LLM 없이 규칙·SQL로 판정" if korean else "judged by rules/SQL, no LLM"),
    ]
    fig, ax = plt.subplots(figsize=(10, 3.4))
    for y, (label, v, n, color, note) in enumerate(reversed(rows)):
        rate = v / n if n else 0
        ax.barh(y, rate * 100, color=color, height=0.5)
        ax.text(max(rate * 100, 0) + 0.1, y, f"  {v}/{n}건 ({rate:.1%})  ·  {note}" if korean
                else f"  {v}/{n} ({rate:.1%})  ·  {note}", va="center", fontsize=10, color=INK)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)])
    ax.set_xlim(0, 12)
    ax.set_xlabel("조건 위반 비율 (%)" if korean else "violation rate (%)")
    ax.set_title("같은 독립 판정(사람이 붙인 우유 라벨·원본 디카페인 필드)으로 잰 조건 위반" if korean
                 else "Condition violations, same independent check", loc="left")
    fig.text(0.01, -0.06, ("지금: 페르소나 4 × 브랜드 17, 추천 최대 3개씩 = %d건" % now["checked"]) if korean
             else "now: 4 personas x 17 brands, up to 3 picks = %d" % now["checked"], fontsize=8, color=MUTED)
    _save(fig, out_path)


# ---------------------------------------------------------------------------
# 03 decaf coverage split by purchasable-in-Korea
# ---------------------------------------------------------------------------

def fig_decaf_coverage(eval_dir: Path, out_path: Path, korean: bool) -> None:
    cmp = _load_here_or_parent(eval_dir, "phase2_compare3.json")["variants"]
    variants = [v for v in ("open", "open_plus") if v in cmp]
    labels = {"open": "오픈 데이터만", "open_plus": "+ 국내 로스터리 (제출본)"} if korean else \
        {"open": "open only", "open_plus": "+ Korean roasteries (submitted)"}
    fig, (ax, ax_t) = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw={"width_ratios": [1, 1.9]})
    for x, v in enumerate(variants):
        by = cmp[v]["decaf_probe"]["by_source"]
        dom = sum(n for s, n in by.items() if s in DOMESTIC_SOURCES)
        total = cmp[v]["decaf_probe"]["candidates"]
        ax.bar(x, dom, color=BLUE, width=0.55)
        ax.bar(x, total - dom, bottom=dom, color=GRAY, width=0.55, edgecolor="white", linewidth=2)
        ax.text(x, total + 0.8, (f"{total}개 (국내 {dom})" if korean else f"{total} (KR {dom})"),
                ha="center", fontsize=10, color=INK)
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels([labels[v] for v in variants])
    ax.set_ylim(0, max(cmp[v]["decaf_probe"]["candidates"] for v in variants) * 1.2)
    ax.set_ylabel("디카페인 원두 후보" if korean else "decaf bean candidates")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=GRAY)],
              labels=["국내에서 구매 가능", "해외 로스터"] if korean else ["buyable in Korea", "overseas"],
              frameon=False, fontsize=9, loc="upper left")
    ax.set_title("국내에서 살 수 있는 디카페인 원두" if korean else "Decaf beans buyable in Korea", loc="left")

    top = (cmp[variants[-1]]["decaf_probe"].get("top") or [])[:5]
    ax_t.axis("off")
    cols = ["#", "원두", "로스터리", "구매", "점수"] if korean else ["#", "Coffee", "Roaster", "Where", "Score"]
    cells = [[str(i + 1), _shorten(t["name"], 24), _shorten(t["roaster"], 16),
              ("국내" if korean else "KR") if t["source"] in DOMESTIC_SOURCES else ("해외" if korean else "abroad"),
              f"{t['score']:.2f}"] for i, t in enumerate(top)]
    if cells:
        tbl = ax_t.table(cellText=cells, colLabels=cols, loc="center", cellLoc="left",
                         colWidths=[0.05, 0.53, 0.24, 0.08, 0.1])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1, 1.6)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#e4e7eb")
            if r == 0:
                cell.set_text_props(weight="bold")
            elif cells[r - 1][3] in ("국내", "KR"):
                cell.set_facecolor("#eaf1fd")
    ax_t.set_title("'디카페인 + 산미' 손님 추천 상위 5 (제출본)" if korean else "Top 5 for a decaf + acidity guest",
                   loc="left")
    _save(fig, out_path)


# ---------------------------------------------------------------------------
# 04 external validation + confidence calibration
# ---------------------------------------------------------------------------

def fig_external_validation(eval_dir: Path, out_path: Path, korean: bool) -> None:
    z = _load_here_or_parent(eval_dir, "phase2_zenodo_external.json")
    loo = _load(eval_dir, "phase2_loo.json")
    model = z["variants"]["open"]["acidity"]
    nbr = z["variants"]["open_neighbours"]["acidity"]
    const = z["baseline_constant_3"]["acidity"]
    fig, (ax, ax_c) = plt.subplots(1, 2, figsize=(12.5, 4.4), gridspec_kw={"width_ratios": [1.5, 1]})
    bars = [
        ("제출본 (특징 모델)" if korean else "submitted (feature model)", model, BLUE),
        ("이웃 평균만" if korean else "neighbour average", nbr, GRAY),
        ("항상 3 (기준선)" if korean else "always 3 (baseline)", const, GRAY),
    ]
    for x, (label, m, color) in enumerate(bars):
        ax.bar(x, m["within1"] * 100, color=color, width=0.55)
        sp = "-" if m.get("spearman") is None else f"{m['spearman']:.2f}"
        ax.text(x, m["within1"] * 100 + 1.5, f"{m['within1']:.1%}\nMAE {m['mae']:.2f} · ρ {sp}\n(n={m['n']})",
                ha="center", va="bottom", fontsize=9, color=INK)
    ax.set_xticks(range(len(bars)))
    ax.set_xticklabels([b[0] for b in bars])
    ax.set_ylim(0, 118)
    ax.set_ylabel("패널 점수와 ±1 이내 (%)" if korean else "within ±1 of panel (%)")
    ax.set_title("외부 검증: 러시아 Q그레이더 패널 %d샘플의 산미 (학습에 안 씀)" % z["samples"] if korean
                 else "External check: acidity vs a Q-grader panel (%d samples, never trained on)" % z["samples"],
                 loc="left")

    conf = loo.get("acidity_within1_by_confidence") or {}
    levels = [lv for lv in ("low", "medium", "high") if lv in conf]
    names = {"low": "낮음", "medium": "보통", "high": "높음"} if korean else {lv: lv for lv in levels}
    for x, lv in enumerate(levels):
        c = conf[lv]
        ax_c.bar(x, c["within1"] * 100, color=BLUE, alpha=0.45 + 0.25 * x, width=0.55)
        ax_c.text(x, c["within1"] * 100 + 1.5, f"{c['within1']:.0%}\n(n={c['n']})", ha="center", fontsize=9,
                  color=INK)
    ax_c.set_xticks(range(len(levels)))
    ax_c.set_xticklabels([names[lv] for lv in levels])
    ax_c.set_ylim(0, 100)
    ax_c.set_xlabel("카드에 보이는 신뢰도" if korean else "confidence shown on the card")
    ax_c.set_title("신뢰도가 높을수록 실제로 더 맞다" if korean else "Higher confidence, more often right", loc="left")
    fig.text(0.01, -0.05, ("외부 패널: Golovinsky 외(2026), Zenodo doi:10.5281/zenodo.20840464, CC BY-NC 4.0 — 평가 전용·집계 "
                           "수치만. 오른쪽: 원두 200개를 하나씩 빼고 나머지로 맞혀 본 산미(CQI 커핑 데이터)." if korean else
                           "Panel: Golovinsky et al. (2026), Zenodo doi:10.5281/zenodo.20840464, CC BY-NC 4.0 - "
                           "evaluation only, aggregates only. Right: 200-bean leave-one-out (acidity)."),
             fontsize=8, color=MUTED)
    _save(fig, out_path)


# ---------------------------------------------------------------------------

def main(eval_dir: Path, out_dir: Path, csv_dir: Path | None = None) -> list[Path]:
    eval_dir, out_dir = Path(eval_dir), Path(out_dir)
    csv_dir = Path(csv_dir) if csv_dir else Path(__file__).resolve().parent.parent.parent / "data" / "competition"
    out_dir.mkdir(parents=True, exist_ok=True)
    korean = _korean_font_available()
    _configure_font(korean)
    jobs = [
        ("01_caffeine_strip.png", lambda p: fig_caffeine_strip(csv_dir, p, korean)),
        ("02_filter_before_after.png", lambda p: fig_filter_before_after(eval_dir, p, korean)),
        ("03_decaf_coverage.png", lambda p: fig_decaf_coverage(eval_dir, p, korean)),
        ("04_external_validation.png", lambda p: fig_external_validation(eval_dir, p, korean)),
    ]
    paths = []
    for filename, render in jobs:
        out_path = out_dir / filename
        render(out_path)
        paths.append(out_path)
    return paths


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("usage: figures.py <eval_dir> <out_dir> [<csv_dir>]", file=sys.stderr)
        sys.exit(2)
    for p in main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) == 4 else None):
        print(p)
