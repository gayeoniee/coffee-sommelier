"""README 수치 검사: README의 핵심 수치를 `data/eval/*.json`과 대조한다.

사용법: `uv run python scripts/check_readme_numbers.py [--readme README.md] [--eval-dir data/eval]`
종료 코드 0 = 모두 일치, 1 = 불일치·누락 목록 출력.

README에서 정규식으로 뽑는 값: 조건 위반 `0/N`, 프랜차이즈 메뉴 수(합계·브랜드별·브랜드 수), 원두 커버리지(전체·디카페인·
태그 보유), LOO 산미/바디 ±1(전체·오픈), 태그 F1, compare3, 설명 품질, 벤치, 학습 수렴.
같은 수치가 README 여러 곳에 나오면 모든 곳을 검사한다. `required` 검사는 README에서 한 번도 안 잡히면 실패다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent

BRAND_KO = {"brand:starbucks": "스타벅스", "brand:mega": "메가", "brand:paik": "빽다방", "brand:paulbassett": "폴바셋",
            "brand:coffeebean": "커피빈", "brand:compose": "컴포즈", "brand:hollys": "할리스"}

NUM = r"\**([\d,]+(?:\.\d+)?)\**"


@dataclass(frozen=True)
class Check:
    name: str
    patterns: tuple[str, ...]
    files: tuple[str, ...]
    expected: Callable[[dict], tuple]
    required: bool = False


def _cov(d: dict) -> dict:
    return d["phase2_coverage.json"]


def _loo(d: dict) -> dict:
    return d["phase2_loo.json"]


def _checks() -> list[Check]:
    cov, loo, c3 = ("phase2_coverage.json",), ("phase2_loo.json",), ("phase2_compare3.json",)
    v3 = lambda d, k: [d["phase2_compare3.json"]["variants"][x]["loo"][k]["within1"]  # noqa: E731
                       for x in ("full", "open", "open_plus")]
    v3body = lambda d, field: [d["phase2_compare3.json"]["variants"][x]["body_loo"]["body"][field]  # noqa: E731
                               for x in ("full", "open", "open_plus")]
    checks = [
        Check("violations", (r"위반율[^\n]*?(\d+)/(\d+)건",), ("phase2_violations.json",),
              lambda d: (d["phase2_violations.json"]["violations"], d["phase2_violations.json"]["checked"]), True),
        Check("menu_brand_count", (r"메뉴 실측 (\d+)개 브랜드",), cov,
              lambda d: (len(_cov(d)["menu_items_by_brand"]),), True),
        Check("menu_total", (r"프랜차이즈 메뉴[^|\n]*\|\s*" + NUM + r"\s*\(",), cov,
              lambda d: (sum(_cov(d)["menu_items_by_brand"].values()),), True),
        Check("coverage_total", (r"원두 \(`coffees`[^|\n]*\|\s*" + NUM, r"원두 수[^|\n]*\|\s*" + NUM), cov,
              lambda d: (_cov(d)["total"],), True),
        Check("coverage_decaf", (r"디카페인 원두\s*\|\s*" + NUM, r"└ 디카페인(?! 메뉴)[^|\n]*\|\s*" + NUM), cov,
              lambda d: (_cov(d)["decaf"],), True),
        Check("coverage_tagged", (r"향미 태그 보유\s*\|\s*" + NUM,), cov, lambda d: (_cov(d)["with_flavor_tags"],)),
        Check("loo_acidity", (r"leave-one-out,? 산미 ±1 이내\s*\|\s*\**([\d.]+)(?: \(n=(\d+)\))?",
                              r"LOO 산미 ±1 이내\s*\|\s*\**([\d.]+) \(n=(\d+)\)"), loo,
              lambda d: (_loo(d)["acidity"]["within1"], _loo(d)["acidity"]["n"]), True),
        Check("loo_body", (r"leave-one-out,? 바디 ±1 이내\s*\|\s*\**([\d.]+)(?: \(n=(\d+)\))?",
                           r"LOO 바디 ±1 이내\s*\|\s*\**([\d.]+) \(n=(\d+)\)"), loo,
              lambda d: (_loo(d)["body"]["within1"], _loo(d)["body"]["n"]), True),
        Check("loo_tag_f1", (r"태그 F1 \(마이크로\)\s*\|\s*([\d.]+)(?: \(n=(\d+)\))?",), loo,
              lambda d: (_loo(d)["tags"]["f1"], _loo(d)["tags"]["n"])),
        Check("loo_category_f1", (r"카테고리 F1\s*\|\s*([\d.]+)(?: \(n=(\d+)\))?",), loo,
              lambda d: (_loo(d)["tags"]["category_f1"], _loo(d)["tags"]["n"])),
        Check("loo_open_acidity", (r"LOO 산미 ±1 이내\s*\|\s*[\d.]+ \(n=\d+\)\s*\|\s*([\d.]+) \(n=(\d+)\)",),
              ("phase2_loo_open.json",),
              lambda d: (d["phase2_loo_open.json"]["acidity"]["within1"], d["phase2_loo_open.json"]["acidity"]["n"])),
        Check("loo_open_body", (r"LOO 바디 ±1 이내\s*\|\s*[\d.]+ \(n=\d+\)\s*\|\s*([\d.]+) \(n=(\d+)\)",),
              ("phase2_loo_open.json",),
              lambda d: (d["phase2_loo_open.json"]["body"]["within1"], d["phase2_loo_open.json"]["body"]["n"])),
        Check("compare3_acidity", (r"LOO 산미 ±1 이내 \(CQI 고정 200개\)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)",),
              c3, lambda d: tuple(v3(d, "acidity"))),
        # body's target set for compare3 is CQI's own 200 acidity targets -- CQI's "Body" is always None
        # (docs/adr/0010-body-heaviness.md -- it was a quality score, not a heaviness fact, and CQI has no
        # review text to re-derive one from), so `loo.body` there is n=0 for every variant, structurally.
        # Body accuracy instead comes from a SEPARATE fixed target set of coffeereview_kaggle beans with a
        # heaviness label (`body_loo.body`, `app.eval.BODY_TARGET_SOURCES`/`body_target_ids`) -- used only as
        # a scoring reference, never served in the app or trained on by the open builds.
        Check("compare3_body", (r"LOO 바디 ±1 이내 \(coffeereview 고정, 채점 전용\)\s*\|\s*"
                                r"([\d.]+) \(n=(\d+)\)\s*\|\s*([\d.]+) \(n=(\d+)\)\s*\|\s*([\d.]+) \(n=(\d+)\)",),
              c3, lambda d: (v3body(d, "within1")[0], v3body(d, "n")[0], v3body(d, "within1")[1],
                            v3body(d, "n")[1], v3body(d, "within1")[2], v3body(d, "n")[2])),
        Check("compare3_body_mae", (r"LOO 바디 MAE \(coffeereview 고정, 채점 전용\)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)",),
              c3, lambda d: tuple(v3body(d, "mae"))),
        Check("explain_rule_pass", (r"규칙 통과 (\d+)/(\d+)",), ("phase2_explain_quality.json",),
              lambda d: (d["phase2_explain_quality.json"]["summary"]["rule_pass"],
                         # scored over LLM-generated texts; template fallbacks are counted, not scored
                         d["phase2_explain_quality.json"]["summary"].get(
                             "generated", d["phase2_explain_quality.json"]["summary"]["n"]))),
        Check("explain_agreement", (r"판정자 2명 합의 (\d+)/(\d+)",), ("phase2_explain_quality.json",),
              lambda d: (d["phase2_explain_quality.json"]["summary"]["no_contradiction_both"],
                         d["phase2_explain_quality.json"]["summary"]["judged_both"])),
        Check("bench", (r"순차 vs 병렬[^\n]*?\|\s*([\d.]+)초 → ([\d.]+)초",), ("phase2_bench.json",),
              lambda d: (d["phase2_bench.json"]["sequential_total_s"], d["phase2_bench.json"]["parallel_total_s"])),
        Check("convergence", (r"프로필 오차\s*\|\s*([\d.]+) → ([\d.]+)",), ("phase2_convergence.json",),
              lambda d: (d["phase2_convergence.json"]["mae_by_step"][0], d["phase2_convergence.json"]["mae_by_step"][-1])),
    ]
    for key, ko in BRAND_KO.items():
        checks.append(Check(f"menu_{key.split(':')[1]}", (rf"프랜차이즈 메뉴[^\n]*?{ko} (\d+)",), cov,
                            lambda d, key=key: (_cov(d)["menu_items_by_brand"].get(key),), True))
    return checks


# Optional checks tied to a headline claim: if their pattern isn't found in the README at
# all (the check is "skipped"), that's not just a stale-README warning — the headline claim
# itself may have silently disappeared, so treat it as a failure.
HEADLINE_CHECKS = frozenset({"explain_rule_pass", "bench", "loo_tag_f1", "compare3_acidity", "compare3_body",
                              "convergence"})


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def find_skipped(readme: str) -> list[str]:
    """Names of optional (non-required) checks whose pattern matches nowhere in the README."""
    return [c.name for c in _checks()
            if not c.required and not any(re.search(pat, readme) for pat in c.patterns)]


def check(readme: str, eval_dir: Path) -> list[str]:
    cache: dict[str, dict | None] = {}

    def load(name: str) -> dict | None:
        if name not in cache:
            p = Path(eval_dir) / name
            cache[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        return cache[name]

    errors = []
    for c in _checks():
        matches = [m for pat in c.patterns for m in re.finditer(pat, readme)]
        if not matches:
            if c.required:
                errors.append(f"{c.name}: README에서 수치를 찾지 못함 (패턴 {c.patterns[0]!r})")
            continue
        missing = [f for f in c.files if load(f) is None]
        if missing:
            errors.append(f"{c.name}: README에는 있는데 JSON이 없음 ({', '.join(missing)})")
            continue
        expected = c.expected({f: load(f) for f in c.files})
        for m in matches:
            for found, want in zip(m.groups(), expected):
                if found is None:
                    continue
                if want is None or abs(_num(found) - float(want)) > 1e-9:
                    line = readme.count("\n", 0, m.start()) + 1
                    errors.append(f"{c.name}: README {line}행 {found.replace(',', '')} ≠ JSON {want} "
                                  f"({', '.join(c.files)})")
    return list(dict.fromkeys(errors))              # one line can match two patterns of a check


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md")
    ap.add_argument("--eval-dir", type=Path, default=ROOT / "data" / "eval")
    a = ap.parse_args(argv)
    readme = a.readme.read_text(encoding="utf-8")
    errors = check(readme, a.eval_dir)
    skipped = find_skipped(readme)
    for e in errors:
        print("MISMATCH", e)
    if skipped:
        print(f"{len(skipped)} checks skipped (pattern not found): {', '.join(skipped)}")
    print(f"{len(_checks())} checks, {len(errors)} problems")
    headline_skipped = [s for s in skipped if s in HEADLINE_CHECKS]
    return 1 if errors or headline_skipped else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
