from pathlib import Path

from scripts.refresh.gates import check_command, check_loo, check_size, check_violations
from scripts.refresh.report import render_markdown
from scripts.refresh.update_numbers import update_draft, update_readme


def test_violations_gate_needs_zero_out_of_something():
    assert check_violations("full", {"violations": 0, "checked": 108}).ok
    assert not check_violations("full", {"violations": 1, "checked": 108}).ok
    assert not check_violations("open", {"violations": 0, "checked": 0}).ok       # nothing checked is no pass


def test_loo_gate_allows_a_small_drop_only():
    base = {"acidity": {"within1": 0.745}}
    assert check_loo("full", {"acidity": {"within1": 0.725}}, base).ok            # -0.02: allowed
    assert not check_loo("full", {"acidity": {"within1": 0.724}}, base).ok
    assert check_loo("open", {"acidity": {"within1": 0.9}}, None).ok              # no baseline yet
    assert not check_loo("open", {}, base).ok


def test_size_and_command_gates():
    assert check_size([]).ok and not check_size(["menu_items: 30%"]).ok
    g = check_command("pytest", 1, "a\nb\n3 failed, 10 passed\n")
    assert not g.ok and "3 failed" in g.detail


def test_update_readme_rewrites_only_data_numbers(tmp_path):
    import json
    (tmp_path / "phase2_violations.json").write_text(json.dumps({"violations": 0, "checked": 120}), encoding="utf-8")
    readme = ("조건 위반율 0/108건\n| 프랜차이즈 메뉴 (메뉴 실측 8개 브랜드) | **1,234** (스타벅스 71) |\n"
              "규칙 통과 20/24\n")
    (tmp_path / "phase2_coverage.json").write_text(json.dumps(
        {"total": 1, "decaf": 1, "with_flavor_tags": 1,
         "menu_items_by_brand": {"brand:starbucks": 75, "brand:mega": 1260}}), encoding="utf-8")
    out, n = update_readme(readme, tmp_path)
    assert "0/120건" in out and "**1,335**" in out and "스타벅스 75" in out
    assert "규칙 통과 20/24" in out                  # explain quality is not a data number: untouched
    assert "메뉴 실측 2개 브랜드" in out and n == 4


def test_update_draft_replaces_only_the_inner_block(tmp_path):
    import json
    (tmp_path / "phase2_violations.json").write_text(json.dumps({"violations": 0, "checked": 7, "rate": 0.0}),
                                                     encoding="utf-8")
    draft = ("앞\n<!-- numbers:start -->\n<!-- numbers:start -->\nold\n<!-- numbers:end -->\n<!-- numbers:end -->\n뒤\n")
    out = update_draft(draft, Path(tmp_path))
    assert out.startswith("앞\n<!-- numbers:start -->\n<!-- numbers:start -->") and out.endswith(
        "<!-- numbers:end -->\n<!-- numbers:end -->\n뒤\n")
    assert "검사 7건 중 위반 0건" in out and "old" not in out


def test_report_markdown_renders_a_failed_run():
    md = render_markdown({"date": "2026-09-28", "dry_run": True, "ok": False, "elapsed_s": 1.0, "errors": ["X: y"],
                          "gates": [{"name": "size_check", "ok": False, "detail": "menu_items: 30%"}],
                          "stages": {"collect": {"mega": "failed: HTTPError"}, "has_diff": False}})
    assert "(DRY_RUN)" in md and "**실패**" in md and "mega | failed: HTTPError" in md and "오류: `X: y`" in md
