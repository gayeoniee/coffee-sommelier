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


def _load_here_or_parent(eval_dir: Path, name: str) -> dict | None:
    for candidate in (Path(eval_dir) / name, Path(eval_dir).parent / name):
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


SWEET_ABSTAIN_RULE = "cue or weighted count >= 1.3 (SHIPPED, ADR 0021)"      # phase10_open_sweetness.json key (re-tuned, ADR 0024)


def render_headline(labels: dict | None, v3: dict | None, zen: dict | None, sweet10: dict | None = None
                    ) -> list[str]:
    """The two accuracy numbers the draft leads with: E1 = leave-one-roaster-out CV on the public roaster gauges
    (phase4_open_labels.json baselines, phase5_open_v3.json shipped model), E2 = the Zenodo external panel
    (phase2_zenodo_external.json, evaluation only). `sweet10` (phase10_open_sweetness.json, ADR 0021) is the
    shipped sweetness-abstention rule when present; without it the sweetness line falls back to phase5_open_v3.json's
    older "cue or neighbour value" rule (ADR 0016), for a data dir that predates ADR 0021."""
    def pct(x) -> str:
        return "-" if x is None else f"{x:.1%}"

    lines = ["**핵심 정확도: 산미 예측** (학습에 쓰지 않은 데이터로만 잰 값)", "",
             "| 검증 | 제출본 모델 ±1 · MAE · 순위상관 | 이웃 평균만 ±1 · MAE | 기준선 ±1 · MAE | n |", "|---|---|---|---|---|"]
    if labels and v3:
        m = v3["abstention"]["acidity"]["answer all (shipped)"]["e1"]
        t = labels["cv"]["acidity"]["table"]
        nb, gm = t["neighbor_avg_new"], t["global_mean"]
        lines.append(f"| E1 로스터리 단위 교차검증(공개 게이지, 한 로스터리는 학습·평가 한쪽에만) | {m['within1']:.3f} · "
                     f"{m['mae']:.3f} · {m['spearman']:.2f} | {nb['within1']:.3f} · {nb['mae']:.3f} | "
                     f"전체 평균 {gm['within1']:.3f} · {gm['mae']:.3f} | {m['n']} |")
    if zen:
        m, nb, c = (zen["variants"]["open"]["acidity"], zen["variants"]["open_neighbours"]["acidity"],
                    zen["baseline_constant_3"]["acidity"])
        lines.append(f"| E2 외부 패널(Zenodo, 평가 전용) | {pct(m['within1'])} · {m['mae']:.2f} · {m['spearman']:.2f} | "
                     f"{pct(nb['within1'])} · {nb['mae']:.2f} (n={nb['n']}) | 항상 3 {pct(c['within1'])} · {c['mae']:.2f} | "
                     f"{m['n']} |")
        b, bc = zen["variants"]["open"]["body"], zen["baseline_constant_3"]["body"]
        if sweet10:
            sw = sweet10["e2"][SWEET_ABSTAIN_RULE]
        else:
            sw = v3["abstention"]["sweetness"]["cue or neighbour value"]["e2"] if v3 else None
        lines += ["", f"- 바디(외부 패널): 제출본 ±1 {pct(b['within1'])}·순위상관 {b['spearman']:.2f} vs 항상 3 "
                      f"{pct(bc['within1'])} — 패널 바디가 거의 3에 몰려 있어 이기지 못함"]
        if sw:
            lines.append(f"- 단맛(외부 패널): 근거 없으면 기권, 답한 비율 {pct(sw['coverage'])}, 답한 것의 ±1 "
                         f"{pct(sw['within1'])}·순위상관 {sw['spearman']:.2f} — 순위가 옮겨 가지 않음")
    return lines


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
        lines += ["", f"**브랜드별 메뉴 수** (메뉴가 있는 브랜드 {len(by_brand)}곳, 합계 {sum(by_brand.values())}종)",
                  "", "| 브랜드 | 메뉴 수 |", "|---|---|"]
        for key in sorted(by_brand):
            brand = key.split(":", 1)[-1]
            lines.append(f"| {brand} | {by_brand[key]} |")
    return lines


def render_violations(v: dict) -> list[str]:
    checked, violations = v.get("checked", 0), v.get("violations", 0)
    rate = v.get("rate", 0.0)
    lines = [
        "**조건 위반** (`phase2_violations.json`)",
        "",
        f"- 검사 {checked}건 중 위반 {violations}건 ({rate:.1%}) — 페르소나 4명 × 브랜드 17곳, 브랜드마다 추천 최대 3개"
        " (메뉴가 없는 브랜드는 원두 기준 추천이라 1~2개)",
    ]
    by_src = v.get("by_milk_label_source")
    if by_src:
        human, protein = by_src.get("human", {}), by_src.get("mfds_protein", {})
        lines.append(
            f"- 우유 판정 근거별(ADR 0023 Phase 2): 사람 라벨 {human.get('checked', 0)}건 중 위반 "
            f"{human.get('violations', 0)}건, MFDS 단백질 신호(사람 라벨이 없는 이름만) {protein.get('checked', 0)}건 중 위반 "
            f"{protein.get('violations', 0)}건")
    return lines


def render_loo(loo: dict) -> list[str]:
    n = loo.get("n", "-")
    seed = loo.get("seed", "-")
    lines = [f"**부록 — 원두 하나씩 빼고 맞히기(LOO), 정답은 CQI 커핑 품질 점수** (n={n}, seed={seed}, `phase2_loo.json`)", "",
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
    lines = ["**데이터 구성 비교** (`phase2_compare3.json`, 산미=CQI 고정 LOO, 바디=coffeereview 고정 별도 대상)", "",
              "| 항목 | " + " | ".join(VARIANT_LABELS.get(v, v) for v in variants) + " |",
              "|---" * (len(variants) + 1) + "|"]

    def row(label: str, get) -> str:
        return f"| {label} | " + " | ".join(_fmt(get(cmp["variants"][v])) for v in variants) + " |"

    lines.append(row("원두 수", lambda d: d["coverage"]["total"]))
    lines.append(row("향미 태그 보유", lambda d: d["coverage"]["with_flavor_tags"]))
    lines.append(row("디카페인", lambda d: d["coverage"]["decaf"]))
    lines.append(row("디카페인 후보(원두 풀 검증)", lambda d: d["decaf_probe"]["candidates"]))
    lines.append(row("LOO 산미 ±1 이내 (CQI 고정 200개)", lambda d: d["loo"]["acidity"]["within1"]))
    lines.append(row("LOO 바디 n (CQI 고정 200개)", lambda d: d["loo"]["body"]["n"]))
    # body has its OWN fixed target set (coffeereview_kaggle beans with a heaviness label -- ADR 0010's
    # CQI-only target set can never score body, n=0 always). coffeereview 라벨은 채점 기준으로만 사용,
    # 앱·학습에는 미사용.
    lines.append(row("LOO 바디 ±1 이내 (coffeereview 고정, 채점 전용)",
                     lambda d: f"{_fmt(d['body_loo']['body']['within1'])} (n={d['body_loo']['body']['n']})"))
    lines.append(row("LOO 바디 MAE (coffeereview 고정, 채점 전용)", lambda d: d["body_loo"]["body"]["mae"]))
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
        f"- 순차 {_fmt(bench.get('sequential_total_s'))}s(첫 호출 콜드 스타트 포함) → 병렬 {_fmt(bench.get('parallel_total_s'))}s",
        f"- 첫 토큰: " + " / ".join(_fmt(t) for t in first) + "s" if first else "- 첫 토큰: -",
    ]


def render_explain_quality(eq: dict) -> list[str]:
    s = eq.get("summary") or {}
    helpful = s.get("helpful_mean") or {}
    return [
        "**설명 품질 판정** (`phase2_explain_quality.json`, n={})".format(s.get("n", "-")),
        "",
        f"- 규칙 통과: {s.get('rule_pass', '-')}/{s.get('generated', s.get('n', '-'))} ({_fmt(s.get('rule_pass_rate'))})"
        + (f" · 가드가 틀로 바꾼 문장 {s['guard_fallbacks']}/{s['n']}" if s.get("guard_fallbacks") else ""),
        f"- 무모순(두 판정자 모두): {_fmt(s.get('no_contradiction_rate_both'))}",
        f"- 무환각(두 판정자 모두): {_fmt(s.get('no_hallucination_rate_both'))}"
        f" (판정자 간 일치율 {_fmt((s.get('judge_agreement') or {}).get('hallucination'))})",
        "- 도움됨 평균: " + ", ".join(f"{k} {v}" for k, v in helpful.items()) if helpful
        else "- 도움됨 평균: -",
    ]


def render_open_v3(tags: dict | None, v3: dict | None, sweet10: dict | None = None) -> list[str]:
    """Open variant v3 (docs/adr/0016-open-variant-v3.md): E1 = grouped leave-one-roaster-out CV on our own labels,
    E2 = the Zenodo external panel (evaluation only). The sweetness rows prefer `sweet10`
    (phase10_open_sweetness.json, ADR 0021's shipped weighted-count abstention rule); without it they fall back to
    phase5_open_v3.json's older "cue or neighbour value" rule (ADR 0016)."""
    def _f3(x) -> str:
        return "-" if x is None else f"{x:.3f}"

    lines = ["**오픈판 v3: E1(로스터리 단위 CV) · E2(Zenodo 외부 패널)** (`phase5_open_tags.json`, `phase5_open_v3.json`)",
             "", "| 항목 | E1 | E2 |", "|---|---|---|"]
    if tags:
        t1, t2 = tags["e1"]["table"], tags["e2"]
        for name, key in (("향미 태그 F1 / 대분류 F1 — 이웃 투표 (노트 없는 입력)", "neighbour_vote"),
                          ("└ 노트 없는 특징 태그 모델 (탑재)", "feat")):
            lines.append(f"| {name} | {_f3(t1[key]['f1'])} / {_f3(t1[key]['category_f1'])} (n={t1[key]['n']}) "
                         f"| {_f3(t2[key]['f1'])} / {_f3(t2[key]['category_f1'])} (n={t2[key]['n']}) |")
    if sweet10 or v3:
        always = "answer all" if sweet10 else "answer all (shipped)"
        shipped = SWEET_ABSTAIN_RULE if sweet10 else "cue or neighbour value"
        for rule, label in ((always, "단맛 — 항상 답함 (이전)"), (shipped, "단맛 — 근거 없으면 기권 (탑재)")):
            r = ({"e1": sweet10["e1"][rule], "e2": sweet10["e2"][rule]} if sweet10
                else v3["abstention"]["sweetness"][rule])
            lines.append(f"| {label}: 답한 비율 · ±1 · MAE | {_f3(r['e1']['coverage'])} · {_f3(r['e1']['within1'])} · "
                         f"{_f3(r['e1']['mae'])} | {_f3(r['e2']['coverage'])} · {_f3(r['e2']['within1'])} · "
                         f"{_f3(r['e2']['mae'])} |")
    if v3:
        b1 = v3["body_e1"]
        shipped = v3.get("body_shipped")
        if shipped in b1:           # ADR 0020: a body recipe ships -> show it, not the best candidate
            best, tag = shipped, "탑재"
        else:
            best = max((k for k in b1 if k != "neighbour_avg"), key=lambda k: (b1[k]["within1"], -b1[k]["mae"]))
            tag = "미탑재"
        e2 = v3["body_e2"][best]
        lines.append(f"| 바디 {'탑재' if tag == '탑재' else '최고 후보'} `{best}` vs 이웃 평균: ±1 ({tag}) | {_f3(b1[best]['within1'])} vs "
                     f"{_f3(b1['neighbour_avg']['within1'])} | {_f3(e2['model']['within1'])} vs "
                     f"{_f3(e2['neighbour']['within1'])} |")
    return lines


def render_open_tag_fill(fill: dict) -> list[str]:
    """Open tag fill (docs/adr/0017-open-tag-fill.md): the current path vs the shipped one, per input situation."""
    rows = (("노트 없는 입력 — E1", "e1_free"), ("노트 없는 입력 — E2", "e2_free"),
            ("노트 한 단어 입력 — E1", "e1_notes"), ("노트 한 단어 입력 — E2", "e2_notes"))
    lines = ["**오픈판 향미 태그 채우기: 이전 → 탑재** (`phase6_open_tag_fill.json`)", "",
             "| 입력 | 정밀도 | 재현율 | 태그 F1 | 대분류 F1 | 원두당 태그 수 |", "|---|---|---|---|---|---|"]
    for label, key in rows:
        cur, new = fill["summary"][key]["current"], fill["summary"][key]["shipped"]
        lines.append(f"| {label} | " + " | ".join(
            f"{cur[c]:.3f} → {new[c]:.3f}" if c != "tags_shown" else f"{cur[c]:.2f} → {new[c]:.2f}"
            for c in ("precision", "recall", "f1", "category_f1", "tags_shown")) + " |")
    return lines


def render_open_tag_cooc(cooc: dict) -> list[str]:
    """Open tag co-occurrence fill (docs/adr/0018-open-tag-cooccurrence.md): current main vs shipped, per input."""
    rows = (("노트 없는 입력 — E1", "e1_free"), ("노트 없는 입력 — E2", "e2_free"),
            ("노트 한 단어 입력 — E1", "e1_notes1"), ("노트 한 단어 입력 — E2", "e2_notes1"),
            ("노트 전체 입력 — E1", "e1_notesall"), ("노트 전체 입력 — E2", "e2_notesall"))
    lines = ["**오픈판 향미 태그 공기 채우기: 이전 → 탑재** (`phase7_open_tag_cooc.json`)", "",
             "| 입력 | 정밀도 | 태그 F1 | 대분류 F1 | 원두당 태그 수 | 태그 1개 이하 |", "|---|---|---|---|---|---|"]
    for label, key in rows:
        if key not in cooc["summary"]:
            continue
        b, a = cooc["summary"][key]["before"], cooc["summary"][key]["after"]
        cells = [f"{b[c]:.3f} → {a[c]:.3f}" for c in ("precision", "f1", "category_f1")]
        cells.append(f"{b['tags_shown']:.2f} → {a['tags_shown']:.2f}")
        cells.append(f"{b['share_le1'] * 100:.1f}% → {a['share_le1'] * 100:.1f}%")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


def main(eval_dir: str | Path) -> str:
    eval_dir = Path(eval_dir)
    parts: list[str] = [NUMBERS_START, "", "### 수치 (자동 생성 — scripts/competition/render_numbers.py, 손으로 고치지 마세요)", ""]

    labels, v3z = _load(eval_dir, "phase4_open_labels.json"), _load(eval_dir, "phase5_open_v3.json")
    zen = _load_here_or_parent(eval_dir, "phase2_zenodo_external.json")
    sweet10 = _load(eval_dir, "phase10_open_sweetness.json")            # ADR 0021 shipped abstention rule
    if (labels and v3z) or zen:
        parts += render_headline(labels, v3z, zen, sweet10) + [""]

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

    open_tags, open_v3 = _load(eval_dir, "phase5_open_tags.json"), _load(eval_dir, "phase5_open_v3.json")
    if open_tags is not None or open_v3 is not None:
        parts += render_open_v3(open_tags, open_v3, sweet10) + [""]

    fill = _load(eval_dir, "phase6_open_tag_fill.json")
    if fill is not None and fill.get("summary"):
        parts += render_open_tag_fill(fill) + [""]

    cooc = _load(eval_dir, "phase7_open_tag_cooc.json")
    if cooc is not None and cooc.get("summary"):
        parts += render_open_tag_cooc(cooc) + [""]

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
