import json
from pathlib import Path

from scripts.check_links import find_broken
from scripts.check_links import main as links_main
from scripts.check_readme_numbers import check
from scripts.check_readme_numbers import main as numbers_main


# ---------- check_links ----------

def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_reports_only_missing_relative_links(tmp_path):
    _write(tmp_path / "docs" / "exists.md", "# ok")
    _write(tmp_path / "img" / "a.png", "x")
    md = _write(tmp_path / "README.md", "\n".join([
        "[ok](docs/exists.md) [ok anchor](docs/exists.md#section) ![img](img/a.png)",
        "[gone](docs/missing.md) ![gone img](img/missing.png)",
        "[web](https://example.com/x.md) [mail](mailto:a@b.c) [anchor](#top)",
        "[titled](docs/exists.md \"title\") [angle](<docs/exists.md>)",
        "<img src=\"img/nothere.png\">",
    ]))
    broken = find_broken([md], root=tmp_path)
    assert sorted(link for _, link in broken) == ["docs/missing.md", "img/missing.png", "img/nothere.png"]


def test_ignores_links_inside_code(tmp_path):
    md = _write(tmp_path / "a.md", "```\n[x](nope.md)\n```\n`[y](nope2.md)` text\n")
    assert find_broken([md], root=tmp_path) == []


def test_resolves_relative_to_the_file_and_skips_paths_outside_root(tmp_path):
    _write(tmp_path / "README.md", "# root")
    md = _write(tmp_path / "docs" / "adr" / "0001.md", "[up](../../README.md) [gh](../../../../tree/v1)")
    assert find_broken([md], root=tmp_path) == []


def test_main_exit_codes(tmp_path, capsys):
    good = _write(tmp_path / "good.md", "[self](good.md)")
    bad = _write(tmp_path / "bad.md", "[x](missing.md)")
    assert links_main([str(good), "--root", str(tmp_path)]) == 0
    assert links_main([str(bad), "--root", str(tmp_path)]) == 1
    assert "missing.md" in capsys.readouterr().out


# ---------- check_readme_numbers ----------

EVAL = {
    "phase2_violations.json": {"checked": 102, "violations": 0},
    "phase2_coverage.json": {
        "total": 9155, "with_flavor_tags": 7534, "decaf": 168,
        "menu_items_by_brand": {"brand:starbucks": 71, "brand:mega": 119, "brand:paik": 114,
                                "brand:paulbassett": 56, "brand:coffeebean": 42, "brand:compose": 42,
                                "brand:hollys": 27},
    },
    "phase2_loo.json": {"acidity": {"n": 200, "within1": 0.735}, "body": {"n": 200, "within1": 0.64},
                        "tags": {"n": 168, "f1": 0.4082, "category_f1": 0.6418}},
}

GOOD_README = """
| 조건 위반율 (top3) | 0.0% — 0/102건 (메뉴 실측 7개 브랜드) |
| 원두 (`coffees`, 전부 1024차원 임베딩) | **9,155** |
| └ 디카페인 원두 | 168 |
| 프랜차이즈 메뉴 (`menu_items`) | 471 (스타벅스 71 · 메가 119 · 빽다방 114 · 폴바셋 56 · 커피빈 42 · 컴포즈 42 · 할리스 27) |
| 원두 예측 leave-one-out, 산미 ±1 이내 | 0.735 (n=200) |
| 원두 예측 leave-one-out, 바디 ±1 이내 | 0.64 |
| LOO 향미 태그 F1 (마이크로) | 0.4082 (n=168) |
"""


def _eval_dir(tmp_path: Path) -> Path:
    d = tmp_path / "eval"
    d.mkdir()
    for name, data in EVAL.items():
        (d / name).write_text(json.dumps(data), encoding="utf-8")
    return d


def test_matching_readme_has_no_errors(tmp_path):
    assert check(GOOD_README, _eval_dir(tmp_path)) == []


def test_catches_stale_numbers(tmp_path):
    stale = (GOOD_README.replace("**9,155**", "**9,048**").replace("0/102건", "0/78건")
             .replace("| 0.735 (n=200)", "| 0.76 (n=200)").replace("컴포즈 42", "컴포즈 40")
             .replace("| 471 (", "| 469 ("))
    errors = "\n".join(check(stale, _eval_dir(tmp_path)))
    for bad in ("9048", "78", "0.76", "40", "469"):
        assert bad in errors


def test_missing_required_number_is_an_error(tmp_path):
    no_violation = "\n".join(line for line in GOOD_README.splitlines() if "위반율" not in line)
    errors = check(no_violation, _eval_dir(tmp_path))
    assert any("violations" in e for e in errors)


def test_numbers_main_exit_codes(tmp_path):
    d = _eval_dir(tmp_path)
    good = tmp_path / "GOOD.md"
    good.write_text(GOOD_README, encoding="utf-8")
    bad = tmp_path / "BAD.md"
    bad.write_text(GOOD_README.replace("| 168 |", "| 161 |"), encoding="utf-8")
    assert numbers_main(["--readme", str(good), "--eval-dir", str(d)]) == 0
    assert numbers_main(["--readme", str(bad), "--eval-dir", str(d)]) == 1
