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
        Check("compare3_body", (r"LOO 바디 ±1 이내\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|",),
              c3, lambda d: tuple(v3(d, "body"))),
        Check("explain_rule_pass", (r"규칙 통과 (\d+)/(\d+)",), ("phase2_explain_quality.json",),
              lambda d: (d["phase2_explain_quality.json"]["summary"]["rule_pass"],
                         d["phase2_explain_quality.json"]["summary"]["n"])),
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


def _num(s: str) -> float:
    return float(s.replace(",", ""))


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
    errors = check(a.readme.read_text(encoding="utf-8"), a.eval_dir)
    for e in errors:
        print("MISMATCH", e)
    print(f"{len(_checks())} checks, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
