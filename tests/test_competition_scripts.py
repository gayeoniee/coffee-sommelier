"""Tests for scripts/competition/{figures,render_numbers,check_draft}.py.

Uses small fake eval JSONs in tmp_path so the tests don't depend on data/eval/open/
(the real open-variant eval results are produced separately, by a different pipeline run).
"""
import json
from pathlib import Path

import pytest

from scripts.competition import check_draft, figures, render_numbers


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_fake_eval_dir(eval_dir: Path, *, with_explain_quality: bool = False) -> None:
    eval_dir.mkdir(parents=True, exist_ok=True)
    _write(eval_dir / "phase2_violations.json", {
        "checked": 4,
        "violations": 1,
        "rate": 0.25,
        "details": [{"persona": "디카페인+산미", "brand": "hollys", "item": "아메리카노"}],
    })
    _write(eval_dir / "phase2_coverage.json", {
        "exclude_sources": [],
        "total": 20,
        "with_embedding": 20,
        "with_flavor_tags": 15,
        "with_acidity": 18,
        "decaf": 3,
        "decaf_with_flavor_tags": 3,
        "menu_items_by_brand": {"brand:hollys": 5, "brand:starbucks": 7},
    })
    _write(eval_dir / "phase2_loo.json", {
        "n": 10, "seed": 1, "exclude_sources": [], "target_sources": "all",
        "targets": 10, "target_ids_sha1": "abc", "neighbor_source_share": {"cqi": 1.0},
        "predictions_with_tags": 0.5, "embedding_model": "fake-embed",
        "acidity": {"n": 10, "exact": 0.4, "within1": 0.7},
        "body": {"n": 10, "exact": 0.3, "within1": 0.6},
        "sweetness": {"n": 5, "exact": 0.5, "within1": 0.8},
        "acidity_within1_by_confidence": {
            "low": {"n": 2, "within1": 0.5},
            "medium": {"n": 6, "within1": 0.7},
            "high": {"n": 2, "within1": 0.9},
        },
    })
    _write(eval_dir / "phase2_compare3.json", {
        "variants": {
            "full": {
                "exclude_sources": [],
                "coverage": {"total": 20, "with_embedding": 20, "with_flavor_tags": 15,
                             "with_acidity": 18, "decaf": 3, "decaf_with_flavor_tags": 3},
                "loo": {
                    "n": 10, "seed": 1, "exclude_sources": [], "target_sources": ["cqi"],
                    "targets": 10, "target_ids_sha1": "abc", "neighbor_source_share": {"cqi": 1.0},
                    "predictions_with_tags": 0.5, "embedding_model": "fake-embed",
                    "acidity": {"n": 10, "exact": 0.4, "within1": 0.7, "mae": 0.9},
                    "body": {"n": 0, "exact": None, "within1": None, "mae": None},  # CQI: body always None
                    "sweetness": {"n": 5, "exact": 0.5, "within1": 0.8, "mae": 0.5},
                    "acidity_within1_by_confidence": {
                        "low": {"n": 2, "within1": 0.5},
                        "medium": {"n": 6, "within1": 0.7},
                        "high": {"n": 2, "within1": 0.9},
                    },
                },
                "body_loo": {
                    "n": 10, "seed": 42, "exclude_sources": [], "target_sources": ["coffeereview_kaggle"],
                    "targets": 10, "target_ids_sha1": "body-abc", "neighbor_source_share": {"coffeereview_kaggle": 1.0},
                    "predictions_with_tags": 0.9, "embedding_model": "fake-embed",
                    "acidity": {"n": 8, "exact": 0.4, "within1": 0.75, "mae": 0.7},
                    "body": {"n": 10, "exact": 0.3, "within1": 0.6, "mae": 0.9},
                    "sweetness": {"n": 6, "exact": 0.4, "within1": 0.7, "mae": 0.6},
                },
                "decaf_probe": {
                    "persona": "디카페인+산미", "candidates": 3, "with_evidence": 3,
                    "by_source": {"cqi": 3},
                    "top": [
                        {"name": "원두A", "roaster": "로스터A", "source": "cqi", "score": 0.9,
                         "acidity": 4, "body": 3, "sweetness": 3, "tags": ["fruity"]},
                        {"name": "원두B", "roaster": "로스터B", "source": "cqi", "score": 0.8,
                         "acidity": 4, "body": 3, "sweetness": 3, "tags": ["floral"]},
                    ],
                },
            },
            "open": {
                "exclude_sources": ["coffeereview_kaggle"],
                "coverage": {"total": 10, "with_embedding": 10, "with_flavor_tags": 5,
                             "with_acidity": 8, "decaf": 1, "decaf_with_flavor_tags": 1},
                "loo": {
                    "n": 10, "seed": 1, "exclude_sources": ["coffeereview_kaggle"],
                    "target_sources": ["cqi"], "targets": 10, "target_ids_sha1": "def",
                    "neighbor_source_share": {"cqi": 1.0}, "predictions_with_tags": 0.2,
                    "embedding_model": "fake-embed",
                    "acidity": {"n": 10, "exact": 0.3, "within1": 0.5, "mae": 1.0},
                    "body": {"n": 0, "exact": None, "within1": None, "mae": None},  # CQI: body always None
                    "sweetness": {"n": 0, "exact": None, "within1": None, "mae": None},
                    "acidity_within1_by_confidence": {
                        "low": {"n": 3, "within1": 0.3},
                        "medium": {"n": 5, "within1": 0.5},
                        "high": {"n": 2, "within1": 0.7},
                    },
                },
                "body_loo": {
                    "n": 10, "seed": 42, "exclude_sources": ["coffeereview_kaggle"],
                    "target_sources": ["coffeereview_kaggle"], "targets": 10, "target_ids_sha1": "body-abc",
                    "neighbor_source_share": {"roasterdb": 1.0}, "predictions_with_tags": 0.4,
                    "embedding_model": "fake-embed",
                    "acidity": {"n": 5, "exact": 0.2, "within1": 0.4, "mae": 1.2},
                    "body": {"n": 9, "exact": 0.2, "within1": 0.5, "mae": 1.1},
                    "sweetness": {"n": 3, "exact": 0.3, "within1": 0.5, "mae": 0.9},
                },
                "decaf_probe": {
                    "persona": "디카페인+산미", "candidates": 1, "with_evidence": 1,
                    "by_source": {"cqi": 1},
                    "top": [
                        {"name": "원두C", "roaster": "로스터C", "source": "cqi", "score": 0.6,
                         "acidity": 3, "body": 3, "sweetness": 3, "tags": []},
                    ],
                },
            },
        }
    })
    _write(eval_dir / "phase2_convergence.json", {
        "users": 5, "steps": 3, "mae_by_step": [1.0, 0.8, 0.6], "improvement": 0.4,
    })
    _write(eval_dir / "phase2_bench.json", {
        "model": "fake-model", "sequential_total_s": 2.0, "parallel_total_s": 1.0,
        "first_token_s": [0.1, 0.2], "full_answer_s": [0.5, 0.6],
    })
    if with_explain_quality:
        _write(eval_dir / "phase2_explain_quality.json", {
            "models": {"explain": "m", "judge": "j", "judge2": "j2"},
            "deadline_s": 12.0, "cases_file": "x.yaml",
            "summary": {
                "n": 4, "generated": 4, "fallbacks": 0, "rule_pass": 3, "rule_pass_rate": 0.75,
                "rule_failures": {"foreign_words": 0, "length": 1, "numbers_grounded": 0,
                                  "condition_mentioned": 0, "polarity": 0},
                "judged_both": 4, "no_contradiction_both": 4, "no_contradiction_rate_both": 1.0,
                "no_hallucination_both": 3, "no_hallucination_rate_both": 0.75,
                "helpful_mean": {"judge": 4.5, "judge2": 4.2},
                "judge_agreement": {"contradiction": 1.0, "hallucination": 0.75},
                "judge_failures": {"judge": 0, "judge2": 0},
                "first_token_p50": 0.5, "first_token_p95": 1.2,
            },
            "cases": [],
        })


# ---------------------------------------------------------------------------
# figures.py
# ---------------------------------------------------------------------------

def _write_fake_figure_inputs(tmp_path: Path) -> tuple[Path, Path]:
    eval_dir = tmp_path / "eval"
    _write_fake_eval_dir(eval_dir, with_explain_quality=True)
    cmp = json.loads((eval_dir / "phase2_compare3.json").read_text(encoding="utf-8"))
    cmp["variants"]["open_plus"] = json.loads(json.dumps(cmp["variants"]["open"]))
    cmp["variants"]["open_plus"]["decaf_probe"].update(
        {"candidates": 3, "by_source": {"roasters_kr": 2, "shopify_gauged": 1}})
    cmp["variants"]["open_plus"]["decaf_probe"]["top"][0]["source"] = "roasters_kr"
    _write(eval_dir / "phase2_compare3.json", cmp)
    metric = {"n": 10, "mae": 0.6, "within1": 0.8, "spearman": 0.6}
    _write(eval_dir / "phase2_zenodo_external.json", {
        "samples": 10,
        "variants": {"open": {"acidity": metric}, "open_neighbours": {"acidity": dict(metric, within1=0.6)}},
        "baseline_constant_3": {"acidity": dict(metric, within1=0.65, spearman=None)},
    })
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "04_데이터셋_menu_items.csv").write_text(
        "brand_key,name,is_decaf,caffeine_mg,source_url\n"
        "brand:a,아메리카노,false,150,u\nbrand:a,디카페인 카페모카,true,136.7,u\n"
        "brand:b,빅 라떼,false,495,u\nbrand:b,디카페인 아메리카노,true,9,u\nbrand:b,모름,false,,u\n",
        encoding="utf-8-sig")
    (csv_dir / "05_데이터셋_brands.csv").write_text(
        "key,name,decaf_available,decaf_surcharge_krw,source_url,verified_at\n"
        "brand:a,에이,true,,u,2026-01-01\nbrand:b,비,true,300,u,2026-01-01\n", encoding="utf-8-sig")
    return eval_dir, csv_dir


class TestFigures:
    def test_main_writes_four_pngs(self, tmp_path):
        eval_dir, csv_dir = _write_fake_figure_inputs(tmp_path)
        paths = figures.main(eval_dir, tmp_path / "images", csv_dir)
        assert sorted(p.name for p in paths) == [
            "01_caffeine_strip.png", "02_filter_before_after.png", "03_decaf_coverage.png",
            "04_external_validation.png",
        ]
        for p in paths:
            assert p.exists() and p.stat().st_size > 0

    def test_english_fallback_when_korean_font_missing(self, tmp_path, monkeypatch):
        eval_dir, csv_dir = _write_fake_figure_inputs(tmp_path)
        monkeypatch.setattr(figures, "_korean_font_available", lambda: False)
        paths = figures.main(eval_dir, tmp_path / "images", csv_dir)
        assert all(p.exists() and p.stat().st_size > 0 for p in paths)

    def test_caffeine_strip_counts_problem_drinks(self, tmp_path):
        _, csv_dir = _write_fake_figure_inputs(tmp_path)
        stats = figures.fig_caffeine_strip(csv_dir, tmp_path / "01.png", korean=True)
        assert stats == {"drinks": 4, "over_300": 1, "decaf_over_30": 1}


# ---------------------------------------------------------------------------
# render_numbers.py
# ---------------------------------------------------------------------------

class TestRenderNumbers:
    def test_includes_violation_count_and_coffee_count(self, tmp_path):
        eval_dir = tmp_path / "eval"
        _write_fake_eval_dir(eval_dir, with_explain_quality=True)

        text = render_numbers.main(eval_dir)

        assert "<!-- numbers:start -->" in text
        assert "<!-- numbers:end -->" in text
        # violation count (1 out of 4 checked, from phase2_violations.json)
        assert "1" in text and "4" in text
        # total coffee count (from phase2_coverage.json)
        assert "20" in text

    def test_handles_missing_explain_quality(self, tmp_path):
        eval_dir = tmp_path / "eval"
        _write_fake_eval_dir(eval_dir, with_explain_quality=False)

        text = render_numbers.main(eval_dir)

        assert "<!-- numbers:start -->" in text
        assert "<!-- numbers:end -->" in text

    def test_handles_missing_compare3(self, tmp_path):
        eval_dir = tmp_path / "eval"
        _write_fake_eval_dir(eval_dir, with_explain_quality=False)
        (eval_dir / "phase2_compare3.json").unlink()

        text = render_numbers.main(eval_dir)

        assert "<!-- numbers:start -->" in text


# ---------------------------------------------------------------------------
# check_draft.py
# ---------------------------------------------------------------------------

DRAFT_HEADER = "<!-- numbers:start -->\nnumbers here\n<!-- numbers:end -->\n"


def _section(key: str, body: str) -> str:
    return f"### {key}. 제목\n{body}\n\n"


def _draft(bodies: dict[str, str], with_markers: bool = True) -> str:
    text = "# Title\n\n" + (DRAFT_HEADER if with_markers else "")
    for key in ("A-2", "A-3", "A-4", "A-5", "A-6"):
        text += _section(key, bodies[key])
    return text


class TestCheckDraft:
    def test_all_sections_long_enough_and_markers_present_returns_zero(self, tmp_path):
        long_body = "가" * 300
        path = tmp_path / "draft.md"
        path.write_text(_draft({k: long_body for k in ("A-2", "A-3", "A-4", "A-5", "A-6")}), encoding="utf-8")

        assert check_draft.main(path) == 0

    def test_short_section_returns_one(self, tmp_path):
        bodies = {k: "가" * 300 for k in ("A-2", "A-3", "A-4", "A-5", "A-6")}
        bodies["A-4"] = "가" * 299  # one short of the 300-char (non-whitespace) requirement
        path = tmp_path / "draft.md"
        path.write_text(_draft(bodies), encoding="utf-8")

        assert check_draft.main(path) == 1

    def test_missing_numbers_markers_returns_one(self, tmp_path):
        long_body = "가" * 300
        path = tmp_path / "draft.md"
        path.write_text(_draft({k: long_body for k in ("A-2", "A-3", "A-4", "A-5", "A-6")}, with_markers=False),
                         encoding="utf-8")

        assert check_draft.main(path) == 1

    def test_counts_remaining_todo_markers(self):
        text = "blah 【작성 필요】 blah 【작성 필요: X】 blah"
        assert check_draft.count_todo(text) == 2

    def test_non_whitespace_length_ignores_spaces_and_newlines(self):
        assert check_draft.non_ws_len("a b\nc\t d") == 4

    def test_parse_sections_extracts_a2_to_a6_bodies(self):
        text = _draft({k: f"body-{k}" for k in ("A-2", "A-3", "A-4", "A-5", "A-6")})
        sections = check_draft.parse_sections(text)
        assert set(sections) == {"A-2", "A-3", "A-4", "A-5", "A-6"}
        assert "body-A-2" in sections["A-2"]
        assert "body-A-3" not in sections["A-2"]
