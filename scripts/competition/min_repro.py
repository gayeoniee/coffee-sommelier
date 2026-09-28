"""키 없이 CSV만으로 최소 재현 — 포털에 올린 03~06 CSV만 읽어 레시피의 핵심 두 단계를 다시 돌린다.

    uv run python scripts/competition/min_repro.py [--csv-dir data/competition] [--sqlite out.db]

API 키·Postgres·임베딩·LLM이 없어도 된다(표준 라이브러리 sqlite3와 csv만 쓴다).

1. **조건 필터**: CSV를 SQLite에 적재하고, 페르소나 4명 × 메뉴가 있는 브랜드마다 카페인·우유 조건을 SQL WHERE로
   먼저 걸러 상위 3개를 고른 뒤, 고른 음료를 필터와 **다른 코드**(원본 행을 다시 읽는 검사 함수)로 검사해 위반 수를 센다.
   - CSV에는 "디카페인으로 바꿔 주문 가능"(decaf_option) 열이 없어서(메뉴는 사실 필드 5개만 올림) 디카페인 조건은
     디카페인 상품만 후보로 삼는다. 웹앱은 이 열까지 써서 후보가 더 많다.
   - 디카페인 상품이라도 카페인이 30mg을 넘으면(초콜릿·차 샷) 디카페인 조건에서 뺀다 — 앱의 `DECAF_MAX_MG`와 같다.
2. **이웃 속성 예측**: 사람이 붙인 산미 라벨(로스터리 게이지 `gauge`)이 있는 원두마다, **같은 로스터리 원두를 빼고**
   (로스터리 단위 교차검증) 산지·가공·로스팅·향미 태그가 가장 많이 겹치는 원두 10개의 산미 평균으로 예측한다.
   임베딩 대신 겹침(자카드) 유사도를 쓰므로 웹앱의 수치와 같지 않다 — "모르는 값은 이웃으로 채우고 이웃이 얼마나
   엇갈리는지(신뢰도)를 함께 보인다"는 절차를 키 없이 확인하는 용도다. 기준선(전체 평균)과 같이 출력한다.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
from pathlib import Path

DECAF_MAX_MG = 30.0      # app/core/scoring.py 와 같은 값
LOW_CAFFEINE_MG = 100.0  # app/core/scoring.py 와 같은 값
K = 10
TOP_N = 3

PERSONAS = [  # (이름, 카페인 규칙, 우유 가능)
    ("디카페인+산미", "decaf_only", True),
    ("저카페인+우유X", "low", False),
    ("제한없음", "any", True),
    ("디카페인+우유X+단맛", "decaf_only", False),
]

FILES = {
    "coffees": "03_데이터셋_coffees.csv",
    "menu_items": "04_데이터셋_menu_items.csv",
    "brands": "05_데이터셋_brands.csv",
    "milk_labels": "06_데이터셋_milk_labels.csv",
}
BOOL_COLS = {"is_decaf", "decaf_available", "is_milk"}
NUM_COLS = {"acidity", "body", "sweetness", "caffeine_mg", "decaf_surcharge_krw"}


def _value(col: str, raw: str):
    if raw == "":
        return None
    if col in BOOL_COLS:
        return 1 if raw.lower() == "true" else 0
    if col in NUM_COLS:
        return float(raw)
    return raw


def load(csv_dir: Path, conn: sqlite3.Connection) -> dict[str, int]:
    """03~06 CSV를 같은 이름의 SQLite 테이블로 적재한다. 반환값은 테이블별 행 수."""
    counts = {}
    for table, fname in FILES.items():
        with (csv_dir / fname).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
            rows = [tuple(_value(c, r[c]) for c in cols) for r in reader]
            conn.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", rows)
            counts[table] = len(rows)
    conn.commit()
    return counts


# --- 1. 조건 필터 ----------------------------------------------------------------

FILTER_SQL = """
SELECT m.rowid, m.brand_key, m.name, m.is_decaf, m.caffeine_mg
FROM menu_items m LEFT JOIN milk_labels l ON l.name = m.name
WHERE m.brand_key = :brand
  AND (:rule != 'decaf_only' OR (m.is_decaf = 1 AND (m.caffeine_mg IS NULL OR m.caffeine_mg <= :decaf_max)))
  AND (:rule != 'low' OR (m.caffeine_mg IS NOT NULL AND m.caffeine_mg <= :low_max))
  AND (:milk_ok = 1 OR l.is_milk = 0)
ORDER BY m.caffeine_mg IS NULL, m.caffeine_mg, m.name
LIMIT :top_n
"""


def pick(conn: sqlite3.Connection, brand: str, rule: str, milk_ok: bool) -> list[tuple]:
    return conn.execute(FILTER_SQL, {"brand": brand, "rule": rule, "milk_ok": int(milk_ok),
                                     "decaf_max": DECAF_MAX_MG, "low_max": LOW_CAFFEINE_MG,
                                     "top_n": TOP_N}).fetchall()


def violates(row: dict, is_milk: bool | None, rule: str, milk_ok: bool) -> str | None:
    """필터 SQL과 따로 쓴 검사: 원본 행 값만 보고 조건 위반 사유를 돌려준다(없으면 None)."""
    mg = row["caffeine_mg"]
    if rule == "decaf_only" and not row["is_decaf"]:
        return "디카페인 아님"
    if rule == "decaf_only" and mg is not None and mg > DECAF_MAX_MG:
        return f"디카페인 표시지만 {mg:g}mg"
    if rule == "low" and (mg is None or mg > LOW_CAFFEINE_MG):
        return "카페인 100mg 초과 또는 모름"
    if not milk_ok and is_milk is not False:
        return "우유 포함 또는 라벨 없음"
    return None


def run_filter(conn: sqlite3.Connection) -> dict:
    brands = [r[0] for r in conn.execute("SELECT DISTINCT brand_key FROM menu_items ORDER BY brand_key")]
    milk = {n: bool(v) for n, v in conn.execute("SELECT name, is_milk FROM milk_labels")}
    raw = {r[0]: {"is_decaf": bool(r[1]), "caffeine_mg": r[2]}
           for r in conn.execute("SELECT rowid, is_decaf, caffeine_mg FROM menu_items")}
    checked, bad, sample = 0, [], []
    for persona, rule, milk_ok in PERSONAS:
        for brand in brands:
            for rowid, _, name, _, mg in pick(conn, brand, rule, milk_ok):
                checked += 1
                why = violates(raw[rowid], milk.get(name), rule, milk_ok)
                if why:
                    bad.append((persona, brand, name, why))
                if persona == "디카페인+우유X+단맛" and len(sample) < 5:
                    sample.append((brand, name, mg))
    return {"brands": len(brands), "checked": checked, "violations": bad, "sample": sample}


# --- 2. 이웃 속성 예측 ------------------------------------------------------------

def _features(r: dict) -> set[str]:
    f = {f"country:{r['origin_country']}", f"process:{r['process']}", f"roast:{r['roast_level']}"}
    f |= {f"tag:{t}" for t in (r["flavor_tags"] or "").split(";") if t}
    return {x for x in f if not x.endswith(":None")}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def predict_attr(target: dict, pool: list[dict], attr: str = "acidity", k: int = K):
    """같은 로스터리를 뺀 풀에서 겹침이 큰 이웃 k개의 가중 평균과 표준편차(→ 신뢰도)."""
    tf = _features(target)
    scored = sorted(((_jaccard(tf, p["_f"]), p) for p in pool
                     if p[attr] is not None and p["roaster"] != target["roaster"] and p["key"] != target["key"]),
                    key=lambda x: -x[0])[:k]
    scored = [(s, p) for s, p in scored if s > 0]
    if len(scored) < 3:
        return None, "low", []
    w = sum(s for s, _ in scored)
    pred = sum(s * p[attr] for s, p in scored) / w
    spread = statistics.pstdev([p[attr] for _, p in scored])
    conf = "high" if spread <= 0.6 else "medium" if spread <= 1.0 else "low"
    return pred, conf, [p for _, p in scored]


def run_neighbours(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM coffees")]
    conn.row_factory = None
    for r in rows:
        r["_f"] = _features(r)
    targets = [r for r in rows if r["acidity_label_source"] == "gauge"]
    pool = [r for r in rows if r["acidity_label_source"] in ("gauge", "roaster_profile")]
    hits = base_hits = n = 0
    err = base_err = 0.0
    by_conf: dict[str, list[int]] = {}
    example = None
    for t in targets:
        pred, conf, nbrs = predict_attr(t, pool)
        if pred is None:
            continue
        # 기준선: 같은 로스터리를 뺀 풀의 산미 평균(누수 없음). 판정은 앱과 같이 |예측 - 정답| <= 1.
        mean = statistics.mean(p["acidity"] for p in pool if p["roaster"] != t["roaster"])
        n += 1
        ok = abs(pred - t["acidity"]) <= 1
        hits += ok
        err += abs(pred - t["acidity"])
        base_hits += abs(mean - t["acidity"]) <= 1
        base_err += abs(mean - t["acidity"])
        by_conf.setdefault(conf, []).append(ok)
        if example is None and t["is_decaf"]:
            example = (t, pred, conf, nbrs[:3])
    return {"targets": len(targets), "n": n, "within1": hits / n if n else None,
            "baseline_within1": base_hits / n if n else None,
            "mae": err / n if n else None, "baseline_mae": base_err / n if n else None,
            "by_conf": {c: (sum(v) / len(v), len(v)) for c, v in by_conf.items()}, "example": example}


# --- 출력 ---------------------------------------------------------------------------

def main(csv_dir: Path, sqlite_path: str = ":memory:") -> dict:
    conn = sqlite3.connect(sqlite_path)
    counts = load(csv_dir, conn)
    print("[적재] " + ", ".join(f"{t} {n}행" for t, n in counts.items()))
    decaf = conn.execute("SELECT count(*), sum(source IN ('roasters_kr','bluebottle_kr')) FROM coffees "
                         "WHERE is_decaf = 1").fetchone()
    print(f"[원두] 디카페인 {decaf[0]}개 (국내에서 살 수 있는 것 {decaf[1] or 0}개)")

    f = run_filter(conn)
    print(f"[1. 조건 필터] 페르소나 {len(PERSONAS)} × 메뉴 브랜드 {f['brands']}, 상위 {TOP_N}개: "
          f"검사 {f['checked']}건 중 위반 {len(f['violations'])}건")
    for v in f["violations"]:
        print("   위반:", *v)
    print("   예시(디카페인+우유X):", "; ".join(f"{b.split(':')[-1]} {n} {mg:g}mg" if mg is not None
                                             else f"{b.split(':')[-1]} {n} (mg 없음)" for b, n, mg in f["sample"]))

    nb = run_neighbours(conn)
    if nb["n"]:
        print(f"[2. 이웃 예측] 게이지 산미 {nb['targets']}건, 예측 {nb['n']}건(같은 로스터리 제외): "
              f"±1 이내 {nb['within1']:.3f} · MAE {nb['mae']:.3f} "
              f"(기준선 = 다른 로스터리 평균: ±1 {nb['baseline_within1']:.3f} · MAE {nb['baseline_mae']:.3f})")
        print("   신뢰도별 ±1:", ", ".join(f"{c} {w:.2f}(n={m})" for c, (w, m) in sorted(nb["by_conf"].items())))
        if nb["example"]:
            t, pred, conf, nbrs = nb["example"]
            print(f"   예시: {t['roaster']} 「{t['name']}」 실제 산미 {t['acidity']:g} → 예측 {pred:.1f} ({conf}), "
                  f"근거 이웃: " + "; ".join(f"{p['roaster']} {p['name'][:24]}({p['acidity']:g})" for p in nbrs))
    else:
        print("[2. 이웃 예측] 게이지 라벨이 없어 건너뜀")
    conn.close()
    return {"counts": counts, "filter": f, "neighbours": nb}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv-dir", type=Path, default=Path("data/competition"))
    ap.add_argument("--sqlite", default=":memory:", help="SQLite 파일로 남기려면 경로(기본: 메모리)")
    a = ap.parse_args()
    result = main(a.csv_dir, a.sqlite)
    raise SystemExit(1 if result["filter"]["violations"] else 0)
