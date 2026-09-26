"""Render the markdown numbers fragment for docs/competition/data-recipe-draft.md from the
eval JSON files (data/eval/ or data/eval/open/), so numbers in the draft are never hand-typed.

Usage: uv run python scripts/competition/render_numbers.py <eval_dir> [--out <path>]

The output is the text to paste between the draft's own
<!-- numbers:start --> ... <!-- numbers:end --> markers (markers included).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NUMBERS_START = "<!-- numbers:start -->"
NUMBERS_END = "<!-- numbers:end -->"

VARIANT_ORDER = ["full", "open", "open_plus"]
VARIANT_LABELS = {"full": "전체(참고)", "open": "오픈", "open_plus": "오픈+로스터리"}


def _load(eval_dir: Path, name: str) -> dict | None:
    path = Path(eval_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(x, digits: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{digits}g}"
    return str(x)


def render_coverage(cov: dict) -> list[str]:
    lines = ["**커버리지** (`phase2_coverage.json`)", "", "| 항목 | 값 |", "|---|---|"]
    lines.append(f"| 원두 수 | {cov.get('total', '-')} |")
    lines.append(f"| 임베딩 보유 | {cov.get('with_embedding', '-')} |")
    lines.append(f"| 향미 태그 보유 | {cov.get('with_flavor_tags', '-')} |")
    lines.append(f"| 산미 보유 | {cov.get('with_acidity', '-')} |")
    lines.append(f"| 디카페인 | {cov.get('decaf', '-')} |")
    lines.append(f"| 디카페인(향미 태그 보유) | {cov.get('decaf_with_flavor_tags', '-')} |")
    by_brand = cov.get("menu_items_by_brand") or {}
    if by_brand:
        lines += ["", "**브랜드별 메뉴 수**", "", "| 브랜드 | 메뉴 수 |", "|---|---|"]
        for key in sorted(by_brand):
            brand = key.split(":", 1)[-1]
            lines.append(f"| {brand} | {by_brand[key]} |")
    return lines


def render_violations(v: dict) -> list[str]:
    checked, violations = v.get("checked", 0), v.get("violations", 0)
    rate = v.get("rate", 0.0)
    return [
        "**조건 위반** (`phase2_violations.json`)",
        "",
        f"- 검사 {checked}건 중 위반 {violations}건 ({rate:.1%})",
    ]


def render_loo(loo: dict) -> list[str]:
    n = loo.get("n", "-")
    seed = loo.get("seed", "-")
    lines = [f"**LOO 예측 정확도** (n={n}, seed={seed}, `phase2_loo.json`)", "",
              "| 속성 | 정확히 일치 | ±1 이내 | n |", "|---|---|---|---|"]
    for attr, label in (("acidity", "산미"), ("body", "바디"), ("sweetness", "단맛")):
        a = loo.get(attr) or {}
        lines.append(f"| {label} | {_fmt(a.get('exact'))} | {_fmt(a.get('within1'))} | {a.get('n', '-')} |")
    conf = loo.get("acidity_within1_by_confidence") or {}
    if conf:
        lines += ["", "**신뢰도별 산미 ±1 이내 비율**", "", "| 신뢰도 | ±1 이내 | n |", "|---|---|---|"]
        for level in ("low", "medium", "high"):
            c = conf.get(level)
            if c:
                lines.append(f"| {level} | {_fmt(c.get('within1'))} | {c.get('n', '-')} |")
    return lines


def render_compare3(cmp: dict) -> list[str]:
    variants = [v for v in VARIANT_ORDER if v in cmp.get("variants", {})]
    if not variants:
        return []
    lines = ["**데이터 구성 비교** (`phase2_compare3.json`, CQI 고정 LOO)", "",
              "| 항목 | " + " | ".join(VARIANT_LABELS.get(v, v) for v in variants) + " |",
              "|---" * (len(variants) + 1) + "|"]

    def row(label: str, get) -> str:
        return f"| {label} | " + " | ".join(_fmt(get(cmp["variants"][v])) for v in variants) + " |"

    lines.append(row("원두 수", lambda d: d["coverage"]["total"]))
    lines.append(row("향미 태그 보유", lambda d: d["coverage"]["with_flavor_tags"]))
    lines.append(row("디카페인", lambda d: d["coverage"]["decaf"]))
    lines.append(row("디카페인 후보(원두 풀 검증)", lambda d: d["decaf_probe"]["candidates"]))
    lines.append(row("LOO 산미 ±1 이내", lambda d: d["loo"]["acidity"]["within1"]))
    lines.append(row("LOO 바디 ±1 이내", lambda d: d["loo"]["body"]["within1"]))
    return lines


def render_convergence(conv: dict) -> list[str]:
    mae = conv.get("mae_by_step") or []
    start = _fmt(mae[0]) if mae else "-"
    end = _fmt(mae[-1]) if mae else "-"
    return [
        "**개인화 수렴** (`phase2_convergence.json`)",
        "",
        f"- 모의 사용자 {conv.get('users', '-')}명 × 기록 {conv.get('steps', '-')}회: "
        f"MAE {start} → {end} (개선 {_fmt(conv.get('improvement'))})",
    ]


def render_bench(bench: dict) -> list[str]:
    first = bench.get("first_token_s") or []
    return [
        "**설명 생성 지연시간** (`phase2_bench.json`, {})".format(bench.get("model", "-")),
        "",
        f"- 순차 {_fmt(bench.get('sequential_total_s'))}s → 병렬 {_fmt(bench.get('parallel_total_s'))}s",
        f"- 첫 토큰: " + " / ".join(_fmt(t) for t in first) + "s" if first else "- 첫 토큰: -",
    ]


def render_explain_quality(eq: dict) -> list[str]:
    s = eq.get("summary") or {}
    helpful = s.get("helpful_mean") or {}
    return [
        "**설명 품질 판정** (`phase2_explain_quality.json`, n={})".format(s.get("n", "-")),
        "",
        f"- 규칙 통과: {s.get('rule_pass', '-')}/{s.get('n', '-')} ({_fmt(s.get('rule_pass_rate'))})",
        f"- 무모순(두 판정자 모두): {_fmt(s.get('no_contradiction_rate_both'))}",
        f"- 무환각(두 판정자 모두): {_fmt(s.get('no_hallucination_rate_both'))}",
        "- 도움됨 평균: " + ", ".join(f"{k} {v}" for k, v in helpful.items()) if helpful
        else "- 도움됨 평균: -",
    ]


def main(eval_dir: str | Path) -> str:
    eval_dir = Path(eval_dir)
    parts: list[str] = [NUMBERS_START, "", "### 수치 (자동 생성 — scripts/competition/render_numbers.py, 손으로 고치지 마세요)", ""]

    violations = _load(eval_dir, "phase2_violations.json")
    if violations is not None:
        parts += render_violations(violations) + [""]

    coverage = _load(eval_dir, "phase2_coverage.json")
    if coverage is not None:
        parts += render_coverage(coverage) + [""]

    loo = _load(eval_dir, "phase2_loo.json")
    if loo is not None:
        parts += render_loo(loo) + [""]

    compare3 = _load(eval_dir, "phase2_compare3.json")
    if compare3 is not None:
        rows = render_compare3(compare3)
        if rows:
            parts += rows + [""]

    convergence = _load(eval_dir, "phase2_convergence.json")
    if convergence is not None:
        parts += render_convergence(convergence) + [""]

    bench = _load(eval_dir, "phase2_bench.json")
    if bench is not None:
        parts += render_bench(bench) + [""]

    explain_quality = _load(eval_dir, "phase2_explain_quality.json")
    if explain_quality is not None:
        parts += render_explain_quality(explain_quality) + [""]
    else:
        parts += ["**설명 품질 판정**: 데이터 없음 (`phase2_explain_quality.json` 미생성)", ""]

    parts.append(NUMBERS_END)
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: render_numbers.py <eval_dir> [--out <path>]", file=sys.stderr)
        sys.exit(2)
    text = main(sys.argv[1])
    if len(sys.argv) >= 4 and sys.argv[2] == "--out":
        Path(sys.argv[3]).write_text(text, encoding="utf-8")
    else:
        print(text)
