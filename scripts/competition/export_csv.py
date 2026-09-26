"""데이터 경진대회 포털 업로드용 CSV 내보내기.

`docs/superpowers/specs/2026-09-27-competition-design.md` §5, plan Task 4.

- 라이선스 제한 소스(coffeereview, RoasterDB(CC BY-NC))는 coffees 데이터셋에서 제외한다.
- 리뷰·설명 원문 등 자유서술 열(`flavor_summary`)은 어떤 파일에도 넣지 않는다.
- 각 파일은 UTF-8 BOM으로 써서 엑셀에서 바로 열리게 한다.

순수 함수(`rows_for_*`, `build_column_definitions`, `write_csv`)는 인메모리 값만으로 단위 테스트한다
(DB 접속은 `fetch_*`와 `main`에만 있다).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Iterable

import yaml

# coffees.source 중 CSV로 재배포해도 되는 것만 (coffeereview_kaggle, roasterdb 제외).
ALLOWED_COFFEE_SOURCES = ("cqi", "roasters_kr", "shopify")

COFFEE_COLUMNS = [
    "key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level",
    "is_decaf", "decaf_process", "acidity", "body", "sweetness", "flavor_tags",
    "source", "source_url", "collected_at",
]
MENU_ITEM_COLUMNS = [
    "brand_key", "name", "name_en", "category", "is_decaf", "decaf_option", "caffeine_mg",
    "source_url", "collected_at",
]
BRAND_COLUMNS = ["key", "name", "decaf_available", "decaf_surcharge_krw", "source_url", "verified_at"]
MILK_LABEL_COLUMNS = ["name", "is_milk"]
SCA_KO_COLUMNS = ["key", "name_en", "name_ko", "level"]

TABLE_COLUMNS: dict[str, list[str]] = {
    "coffees": COFFEE_COLUMNS,
    "menu_items": MENU_ITEM_COLUMNS,
    "brands": BRAND_COLUMNS,
}

# (테이블, 컬럼) -> (타입, 비고). 컬럼정의 파일(01_컬럼정의.csv)에 그대로 실린다.
COLUMN_INFO: dict[tuple[str, str], tuple[str, str]] = {
    ("coffees", "key"): ("text", "원두 고유 식별자(예: cqi:xyz)"),
    ("coffees", "name"): ("text", "원두 이름"),
    ("coffees", "roaster"): ("text", "로스터리/브랜드명"),
    ("coffees", "origin_country"): ("text", "원산지 국가"),
    ("coffees", "origin_region"): ("text", "원산지 지역"),
    ("coffees", "process"): ("text", "가공 방식(예: washed, natural)"),
    ("coffees", "roast_level"): ("text", "로스팅 정도"),
    ("coffees", "is_decaf"): ("boolean", "디카페인 원두 여부"),
    ("coffees", "decaf_process"): ("text", "디카페인 처리 공정(해당 시)"),
    ("coffees", "acidity"): ("integer", "산미 강도 1~5"),
    ("coffees", "body"): ("integer", "바디감 1~5"),
    ("coffees", "sweetness"): ("integer", "단맛 1~5"),
    ("coffees", "flavor_tags"): ("text[]", "플레이버 태그 목록(세미콜론 구분)"),
    ("coffees", "source"): ("text", f"데이터 출처({', '.join(ALLOWED_COFFEE_SOURCES)})"),
    ("coffees", "source_url"): ("text", "출처 URL"),
    ("coffees", "collected_at"): ("date", "수집일(YYYY-MM-DD)"),
    ("menu_items", "brand_key"): ("text", "브랜드 고유 식별자(05_데이터셋_brands.csv의 key)"),
    ("menu_items", "name"): ("text", "메뉴명"),
    ("menu_items", "name_en"): ("text", "메뉴 영문명(있는 경우)"),
    ("menu_items", "category"): ("text", "메뉴 카테고리"),
    ("menu_items", "is_decaf"): ("boolean", "디카페인 메뉴 여부"),
    ("menu_items", "decaf_option"): ("boolean", "디카페인 옵션 제공 여부"),
    ("menu_items", "caffeine_mg"): ("real", "카페인 함량(mg, 공식 고시 기준)"),
    ("menu_items", "source_url"): ("text", "출처 URL(브랜드 공식 사이트)"),
    ("menu_items", "collected_at"): ("date", "수집일(YYYY-MM-DD)"),
    ("brands", "key"): ("text", "브랜드 고유 식별자"),
    ("brands", "name"): ("text", "브랜드명"),
    ("brands", "decaf_available"): ("boolean", "디카페인 옵션 제공 여부"),
    ("brands", "decaf_surcharge_krw"): ("integer", "디카페인 추가 요금(원, 없으면 공란)"),
    ("brands", "source_url"): ("text", "출처 URL(브랜드 공식 사이트)"),
    ("brands", "verified_at"): ("date", "확인일(YYYY-MM-DD)"),
}

# 02_데이터_템플릿.csv 용 고정 예시(실 데이터에 의존하지 않는 정적 견본).
TEMPLATE_ROWS: dict[str, list[dict[str, Any]]] = {
    "coffees": [
        {"key": "cqi:example-1", "name": "예시 원두 A", "roaster": "예시 로스터리", "origin_country": "Ethiopia",
         "origin_region": "Yirgacheffe", "process": "washed", "roast_level": "light", "is_decaf": False,
         "decaf_process": "", "acidity": 4, "body": 3, "sweetness": 3, "flavor_tags": ["floral", "citrus"],
         "source": "cqi", "source_url": "https://example.com/coffees/1", "collected_at": "2026-01-01"},
        {"key": "roasters_kr:example-2", "name": "예시 원두 B", "roaster": "예시 국내 로스터리",
         "origin_country": "Colombia", "origin_region": "Huila", "process": "natural", "roast_level": "medium",
         "is_decaf": True, "decaf_process": "sugarcane", "acidity": 3, "body": 4, "sweetness": 4,
         "flavor_tags": ["caramel", "nutty"], "source": "roasters_kr",
         "source_url": "https://example.com/coffees/2", "collected_at": "2026-01-02"},
        {"key": "shopify:example-3", "name": "예시 원두 C", "roaster": "예시 브랜드", "origin_country": "Brazil",
         "origin_region": "Cerrado", "process": "pulped natural", "roast_level": "medium-dark", "is_decaf": False,
         "decaf_process": "", "acidity": 2, "body": 4, "sweetness": 3, "flavor_tags": ["chocolate"],
         "source": "shopify", "source_url": "https://example.com/coffees/3", "collected_at": "2026-01-03"},
    ],
    "menu_items": [
        {"brand_key": "example-brand", "name": "예시 아메리카노", "name_en": "Example Americano",
         "category": "coffee", "is_decaf": False, "decaf_option": True, "caffeine_mg": 150.0,
         "source_url": "https://example.com/menu/1", "collected_at": "2026-01-01"},
        {"brand_key": "example-brand", "name": "예시 카페라떼", "name_en": "Example Cafe Latte",
         "category": "coffee", "is_decaf": False, "decaf_option": True, "caffeine_mg": 150.0,
         "source_url": "https://example.com/menu/2", "collected_at": "2026-01-01"},
        {"brand_key": "example-brand", "name": "예시 디카페인 라떼", "name_en": "Example Decaf Latte",
         "category": "coffee", "is_decaf": True, "decaf_option": True, "caffeine_mg": 5.0,
         "source_url": "https://example.com/menu/3", "collected_at": "2026-01-01"},
    ],
    "brands": [
        {"key": "example-brand", "name": "예시 브랜드", "decaf_available": True, "decaf_surcharge_krw": 500,
         "source_url": "https://example.com", "verified_at": "2026-01-01"},
        {"key": "example-brand-2", "name": "예시 브랜드 2", "decaf_available": False, "decaf_surcharge_krw": "",
         "source_url": "https://example.com/2", "verified_at": "2026-01-02"},
        {"key": "example-brand-3", "name": "예시 브랜드 3", "decaf_available": True, "decaf_surcharge_krw": 0,
         "source_url": "https://example.com/3", "verified_at": "2026-01-03"},
    ],
}


# --- 순수 함수: 행 선택/변환(인메모리, DB 접속 없음) ------------------------

def rows_for_coffees(rows: Iterable[dict]) -> list[dict]:
    """`flavor_summary`(원문 파생 요약) 등 컬럼 목록 밖 열을 떨어뜨리고, 제한 소스 행을 제외한다."""
    return [{col: r.get(col) for col in COFFEE_COLUMNS}
            for r in rows if r.get("source") in ALLOWED_COFFEE_SOURCES]


def rows_for_menu_items(rows: Iterable[dict]) -> list[dict]:
    return [{col: r.get(col) for col in MENU_ITEM_COLUMNS} for r in rows]


def rows_for_brands(rows: Iterable[dict]) -> list[dict]:
    """`notes`(자유 서술)와 내부 FK(`default_bean_coffee_id` 등)를 제외한다."""
    return [{col: r.get(col) for col in BRAND_COLUMNS} for r in rows]


def rows_for_milk_labels(labels: dict[str, bool]) -> list[dict]:
    """`data/curated/menu_milk_labels.yaml`(메뉴명 -> 우유 포함 여부)을 행으로 편다."""
    return [{"name": name, "is_milk": is_milk} for name, is_milk in labels.items()]


def rows_for_sca_ko(ko: dict[str, str]) -> list[dict]:
    """`data/curated/sca_ko.yaml`(`상위>하위` 경로 -> 한국어 이름)에서 파생한다.

    SCA 휠 원문(설명 텍스트)에는 의존하지 않는다 — `name_en`은 경로의 마지막 조각,
    `level`은 `>` 개수 + 1로 우리가 직접 계산한다.
    """
    rows = []
    for key, name_ko in ko.items():
        rows.append({
            "key": key,
            "name_en": key.rsplit(">", 1)[-1],
            "name_ko": name_ko,
            "level": key.count(">") + 1,
        })
    return rows


def build_column_definitions() -> list[dict]:
    """01_컬럼정의.csv 행: coffees -> menu_items -> brands 순, 1부터 이어지는 변수번호."""
    defs = []
    n = 0
    for table in ("coffees", "menu_items", "brands"):
        for col in TABLE_COLUMNS[table]:
            n += 1
            dtype, remark = COLUMN_INFO[(table, col)]
            defs.append({
                "변수번호": n, "타깃여부": "N", "타입": dtype,
                "컬럼명": f"{table}.{col}", "비고": remark,
            })
    return defs


def build_template_rows() -> list[list[Any]]:
    """02_데이터_템플릿.csv 내용: 테이블명 표시줄 + 그 테이블 헤더 + 예시 3행, 테이블마다 반복."""
    rows: list[list[Any]] = []
    for table in ("coffees", "menu_items", "brands"):
        cols = TABLE_COLUMNS[table]
        rows.append(["테이블명", table])
        rows.append(list(cols))
        for example in TEMPLATE_ROWS[table]:
            rows.append([to_csv_value(example.get(c)) for c in cols])
        rows.append([])
    return rows[:-1]  # 마지막 구분용 빈 줄은 제거


# --- CSV 직렬화(BOM 포함) ---------------------------------------------------

def to_csv_value(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    return v


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> int:
    """헤더 + 행을 UTF-8 BOM(`utf-8-sig`)으로 쓴다(엑셀 호환). 반환값은 쓴 행 수."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: to_csv_value(r.get(k)) for k in fieldnames})
            n += 1
    return n


def write_raw_csv(path: Path, rows: Iterable[list[Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)
            n += 1
    return n


# --- DB 조회(여기만 DB에 접속한다; 단위 테스트 대상이 아니다) ---------------

def fetch_coffees(conn) -> list[dict]:
    cols = ["key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level",
            "is_decaf", "decaf_process", "acidity", "body", "sweetness", "flavor_tags", "flavor_summary",
            "source", "source_url", "collected_at"]
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM coffees WHERE active ORDER BY key").fetchall()
    return [dict(zip(cols, row)) for row in rows]


def fetch_menu_items(conn) -> list[dict]:
    cols = ["m.name", "m.name_en", "m.category", "m.is_decaf", "m.decaf_option", "m.caffeine_mg",
            "m.source_url", "m.collected_at", "b.key AS brand_key"]
    rows = conn.execute(
        "SELECT " + ", ".join(cols) + " FROM menu_items m JOIN brands b ON b.id = m.brand_id "
        "WHERE m.active AND b.active ORDER BY b.key, m.name"
    ).fetchall()
    names = ["name", "name_en", "category", "is_decaf", "decaf_option", "caffeine_mg",
             "source_url", "collected_at", "brand_key"]
    return [dict(zip(names, row)) for row in rows]


def fetch_brands(conn) -> list[dict]:
    cols = ["key", "name", "decaf_available", "decaf_surcharge_krw", "source_url", "verified_at"]
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM brands WHERE active ORDER BY key").fetchall()
    return [dict(zip(cols, row)) for row in rows]


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# --- 엔트리 포인트 -----------------------------------------------------------

def main(database_url: str, out_dir: Path, curated_dir: Path | None = None) -> dict[str, int]:
    import psycopg

    if curated_dir is None:
        curated_dir = Path(__file__).resolve().parent.parent.parent / "data" / "curated"

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    counts["01_컬럼정의.csv"] = write_csv(
        out_dir / "01_컬럼정의.csv", ["변수번호", "타깃여부", "타입", "컬럼명", "비고"], build_column_definitions())
    counts["02_데이터_템플릿.csv"] = write_raw_csv(out_dir / "02_데이터_템플릿.csv", build_template_rows())

    with psycopg.connect(database_url) as conn:
        coffees = rows_for_coffees(fetch_coffees(conn))
        menu_items = rows_for_menu_items(fetch_menu_items(conn))
        brands = rows_for_brands(fetch_brands(conn))

    counts["03_데이터셋_coffees.csv"] = write_csv(out_dir / "03_데이터셋_coffees.csv", COFFEE_COLUMNS, coffees)
    counts["04_데이터셋_menu_items.csv"] = write_csv(out_dir / "04_데이터셋_menu_items.csv", MENU_ITEM_COLUMNS, menu_items)
    counts["05_데이터셋_brands.csv"] = write_csv(out_dir / "05_데이터셋_brands.csv", BRAND_COLUMNS, brands)

    milk_labels = rows_for_milk_labels(load_yaml(curated_dir / "menu_milk_labels.yaml"))
    counts["06_데이터셋_milk_labels.csv"] = write_csv(out_dir / "06_데이터셋_milk_labels.csv", MILK_LABEL_COLUMNS, milk_labels)

    sca_ko = rows_for_sca_ko(load_yaml(curated_dir / "sca_ko.yaml"))
    counts["07_데이터셋_sca_ko.csv"] = write_csv(out_dir / "07_데이터셋_sca_ko.csv", SCA_KO_COLUMNS, sca_ko)

    return counts


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", dest="database_url", required=True, help="DATABASE_URL (예: coffee_open)")
    p.add_argument("--out-dir", type=Path, default=Path("data/competition"))
    p.add_argument("--curated-dir", type=Path, default=None)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    result = main(args.database_url, args.out_dir, args.curated_dir)
    for name, n in result.items():
        size = (args.out_dir / name).stat().st_size
        print(f"{name}: {n}행, {size:,}바이트")
