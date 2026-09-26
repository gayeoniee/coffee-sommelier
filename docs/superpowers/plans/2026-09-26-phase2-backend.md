# Coffee Sommelier 2단계 — Plan 1: 백엔드 (데이터 변경 · 도메인 로직 · LangGraph · API · 평가)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2단계 설계의 백엔드를 완성한다 — 적재를 key upsert로 전환하고 사용자 테이블·브랜드 원두 속성을 추가, 순수 도메인 로직(필터·점수·예측·학습), LangGraph 그래프 3개, SSE 스트리밍 FastAPI, 트레이싱, 평가 명령, ADR까지.

**Architecture:** `app/core/`는 DB·LLM을 모르는 순수 함수, `app/repo.py`는 동기 psycopg 풀로 DB 접근, `app/graphs/`는 LangGraph로 흐름(분기·폴백·병렬 fan-out)만 담당하고 노드는 core 함수의 얇은 래퍼다. `app/api.py`는 그래프의 custom 스트림을 SSE로 내보낸다. LLM은 1단계 `pipeline/llm.py`의 설정(`config/models.yaml`)을 재사용하는 비동기 스트리밍 클라이언트(`app/llm.py`)로 부른다.

**Tech Stack:** Python 3.12, uv, LangGraph 1.2.x, FastAPI 0.14x + uvicorn, psycopg 3 + psycopg-pool, Langfuse 4.x(키 없으면 비활성), httpx, pydantic 2, pytest. 로컬 Postgres 17 + pgvector(Docker), 로컬 Ollama(qwen3.5:9b, bge-m3), NVIDIA API.

**Spec:** `docs/superpowers/specs/2026-09-26-phase2-recommend-app-design.md` (1단계 스펙: `docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md`)

**Plan 분할:** 이 문서는 Plan 1(백엔드)이다. Plan 2 = Next.js 화면 5개, Plan 3 = NVIDIA 임베딩 전환 + Neon/Render/Vercel 배포 + README. Plan 2·3은 Plan 1 완료 후 별도 작성한다.

## Global Constraints

- Python `>=3.12`, `uv sync` / `uv run ...`. 새 의존성: `langgraph>=1.2,<2`, `fastapi>=0.140`, `uvicorn[standard]>=0.30`, `psycopg-pool>=3.2`, `langfuse>=4.15,<5`. **LangChain은 설치하지 않는다.**
- LangGraph API (1.2.12에서 실측 확인): `from langgraph.graph import StateGraph, START, END`, `from langgraph.types import Send`, `from langgraph.config import get_stream_writer`, 병렬 결과는 `Annotated[list[dict], operator.add]` 리듀서, 스트리밍은 `graph.astream(inputs, stream_mode="custom")`.
- Langfuse 4.x: `langfuse.observe(name=...)` 데코레이터, `get_client().start_as_current_observation(name=..., as_type="span")`. `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`가 없으면 트레이싱 코드를 아예 거치지 않는다(경고 로그 방지).
- DB 접근은 **동기 psycopg + `psycopg_pool.ConnectionPool`**, 그래프 노드에서는 `await asyncio.to_thread(...)`로 호출한다(Windows ProactorEventLoop에서 psycopg async가 동작하지 않기 때문).
- 적재(`pipeline/load.py`)는 **사용자 테이블(users, taste_profiles, tastings, profile_history)을 절대 삭제·TRUNCATE하지 않는다.**
- 산미·바디·단맛은 1~5 실수. 취향 적합도 = 속성 0.6 + 향미 0.4. 하드 조건 위반 추천 0%.
- **리뷰 원문은 API 응답에 절대 포함하지 않는다**(근거는 요약 문장만).
- 쿠키 이름 `cs_uid`, `HttpOnly`, 1년, `Secure`/`SameSite`는 환경변수(`COOKIE_SECURE` 기본 true, `COOKIE_SAMESITE` 기본 lax).
- 테스트는 네트워크·LLM 없이 돈다(가짜 deps). DB 테스트는 `@pytest.mark.db`, Postgres 테스트 DB(`coffee_test`)가 떠 있어야 하며 skip되면 안 된다.
- 셸 명령은 레포 루트에서 실행. 커밋 메시지 끝 트레일러는 세션 지시에 따른다.

## Review Focus

- **알려진 산지 단어가 없는 한국어 원두 입력**("동네 로스터리 하우스 블렌드") → 에러가 아니라 신뢰도 low 카드 + 설명이 나와야 한다. (Task 10 테스트)
- **메뉴 데이터가 없는 브랜드**(투썸·컴포즈 등 7곳) → 추천이 비지 않도록 브랜드 원두로 만든 기본 메뉴(아메리카노·카페라떼)를 후보로 쓴다. (Task 8, 9 테스트)
- **디카페인만 마시는 사용자가 스타벅스**(디카페인 전용 메뉴 0, 디카페인 샷 변경 가능 56) → "디카페인으로 변경" 표시와 추가요금이 카드에 나와야 한다. (Task 8, 9 테스트)
- **쓰레기 쿠키 값**(UUID 아님, 삭제된 사용자) → 500이 아니라 401, `/session`이 새 게스트를 만든다. (Task 12 테스트)
- **LLM 스트림이 중간에 끊김** → 부분 텍스트 대신 `explain_fallback` 템플릿으로 교체된다. 검색어에 `%`·`_`가 섞여도 SQL 와일드카드로 해석되지 않는다. (Task 9, 8 테스트)

## File Structure

```
db/schema.sql                    (+ 사용자 테이블, brands.bean/decaf_bean)
pipeline/db.py                   TABLES에 사용자 테이블 추가(테스트 초기화용)
pipeline/load.py                 key upsert + 사라진 key 삭제(참조 보호)
pipeline/records.py              BrandRecord.bean / decaf_bean (BeanProfile)
data/curated/brands.yaml         브랜드별 bean / decaf_bean
config/models.yaml               explain / parse_note / parse_bean 작업 추가
app/__init__.py
app/config.py                    쿠키·CORS·작업 이름 설정
app/models.py                    Item, Profile, Neighbor, Prediction, ParsedBean
app/tracing.py                   traced(), span() — 키 없으면 no-op
app/core/__init__.py
app/core/flavors.py              SCA 카테고리, 태그→카테고리, 카테고리 벡터, 코사인
app/core/scoring.py              우유 판별, 하드 필터, 속성/향미 적합도, 점수, MMR
app/core/predict.py              이웃 → 예측(평균·신뢰도·향미·근거), 예측 → Item
app/core/learning.py             프로필 갱신
app/core/simulate.py             모의 사용자 수렴 시뮬레이션
app/core/parse.py                원두 텍스트 규칙 파싱, LLM 파싱 스키마(BeanParse, NoteSignals)
app/core/explain.py              템플릿 설명, LLM 메시지, 카드 dict
app/llm.py                       async 스트리밍/JSON 클라이언트
app/repo.py                      Repo (psycopg_pool)
app/graphs/__init__.py           Deps, default_deps
app/graphs/common.py             설명 스트리밍 + 폴백
app/graphs/recommend.py          recommend 그래프
app/graphs/analyze_bean.py       analyze_bean 그래프
app/graphs/log_tasting.py        log_tasting 그래프
app/api.py                       FastAPI 앱 (create_app)
app/eval.py                      평가 명령 (violations / loo / convergence / bench)
docs/adr/0001-fastapi.md, docs/adr/0002-langgraph.md
tests/app/__init__.py, tests/app/fakes.py, tests/app/test_*.py
```

---

### Task 1: 사용자 테이블 + 적재 key upsert 전환

**Files:**
- Modify: `db/schema.sql` (끝에 추가), `pipeline/db.py`, `pipeline/load.py` (전체 교체)
- Test: `tests/test_db_load.py` (기존 기대값 수정 + 신규 테스트)

**Interfaces:**
- Consumes: 1단계 `read_jsonl`, `read_json_lines`, `to_vector_literal`, records
- Produces: 테이블 `users(id uuid)`, `taste_profiles`, `tastings`, `profile_history`; `run_load(conn, norm_dir, enriched_dir, embedded_dir) -> dict` 결과에 `deleted_coffees`, `deleted_reviews`, `deleted_menu_items`, `deleted_brands`, `kept_referenced_coffees` 키 추가. `pipeline.db.TABLES`는 사용자 테이블 포함(테스트 초기화 전용, 운영 코드에서 `reset_tables` 호출 금지).

- [ ] **Step 1: 스키마 추가** — `db/schema.sql` 끝에

```sql
-- ===== 2단계: 사용자 =====
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nickname text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taste_profiles (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  caffeine_rule text NOT NULL CHECK (caffeine_rule IN ('decaf_only', 'low', 'any')),
  milk_ok boolean NOT NULL,
  acidity real NOT NULL,
  body real NOT NULL,
  sweetness real NOT NULL,
  flavor_weights jsonb NOT NULL DEFAULT '{}',
  n_updates integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tastings (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  coffee_id bigint REFERENCES coffees(id),
  menu_item_id bigint REFERENCES menu_items(id),
  input_text text,
  predicted jsonb,
  rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
  note text,
  parsed_signals jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(coffee_id, menu_item_id, input_text) = 1)
);

CREATE TABLE IF NOT EXISTS profile_history (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  snapshot jsonb NOT NULL,
  tasting_id bigint REFERENCES tastings(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tastings_user_idx ON tastings (user_id, id DESC);
CREATE INDEX IF NOT EXISTS profile_history_user_idx ON profile_history (user_id, id DESC);
```

- [ ] **Step 2: `pipeline/db.py` TABLES 교체**

```python
# Test-fixture reset only. Production code must never truncate user tables.
TABLES = ["profile_history", "tastings", "taste_profiles", "users",
          "menu_items", "brands", "reviews", "coffees", "flavor_taxonomy", "enrich_log"]
```

- [ ] **Step 3: 실패하는 테스트 작성** — `tests/test_db_load.py`

`test_load_and_query`의 기대 dict를 다음으로 바꾼다:
```python
    assert counts == {"coffees": 2, "reviews": 1, "brands": 1, "menu_items": 1, "flavor_taxonomy": 2,
                      "enrich_log": 1, "dropped_reviews": 0, "dropped_menu_items": 0,
                      "deleted_coffees": 0, "deleted_reviews": 0, "deleted_menu_items": 0, "deleted_brands": 0,
                      "kept_referenced_coffees": 0}
```
파일 끝에 추가:
```python
def _add_tasting(conn, coffee_key=None, menu_key=None):
    uid = conn.execute("INSERT INTO users DEFAULT VALUES RETURNING id").fetchone()[0]
    cid = conn.execute("SELECT id FROM coffees WHERE key = %s", (coffee_key,)).fetchone()[0] if coffee_key else None
    mid = conn.execute("SELECT id FROM menu_items WHERE key = %s", (menu_key,)).fetchone()[0] if menu_key else None
    conn.execute("INSERT INTO tastings (user_id, coffee_id, menu_item_id, rating) VALUES (%s, %s, %s, 4)", (uid, cid, mid))
    conn.commit()
    return uid


def test_reload_keeps_ids_and_user_data(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    ids_before = dict(db_conn.execute("SELECT key, id FROM coffees").fetchall())
    uid = _add_tasting(db_conn, coffee_key="c1")
    _add_tasting(db_conn, menu_key="m1")
    run_load(db_conn, norm, enriched, embedded)
    assert dict(db_conn.execute("SELECT key, id FROM coffees").fetchall()) == ids_before
    assert db_conn.execute("SELECT count(*) FROM tastings").fetchone()[0] == 2
    assert db_conn.execute("SELECT count(*) FROM users WHERE id = %s", (uid,)).fetchone()[0] == 1


def test_reload_deletes_missing_rows_but_keeps_referenced(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    _add_tasting(db_conn, coffee_key="c1")
    write_jsonl(enriched / "coffees.jsonl", [])          # both coffees vanish from the source
    write_jsonl(norm / "reviews.jsonl", [])
    counts = run_load(db_conn, norm, enriched, embedded)
    assert counts["deleted_coffees"] == 1                # c2 deleted
    assert counts["kept_referenced_coffees"] == 1        # c1 kept: a tasting points at it
    assert counts["deleted_reviews"] == 1
    keys = {k for (k,) in db_conn.execute("SELECT key FROM coffees").fetchall()}
    assert keys == {"c1"}


def test_updated_values_are_written(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    write_jsonl(enriched / "coffees.jsonl", [
        CoffeeRecord(key="c1", name="Ethiopia Washed v2", origin_country="Ethiopia", acidity=3,
                     source="t", collected_at="2026-09-26"),
        CoffeeRecord(key="c2", name="Ethiopia Decaf", origin_country="Ethiopia", is_decaf=True,
                     source="t", collected_at="2026-09-24")])
    run_load(db_conn, norm, enriched, embedded)
    assert db_conn.execute("SELECT name, acidity FROM coffees WHERE key = 'c1'").fetchone() == ("Ethiopia Washed v2", 3)
```

- [ ] **Step 4: 실패 확인**

Run: `docker compose up -d db && uv run pytest tests/test_db_load.py -v`
Expected: FAIL — `test_load_and_query`(키 없음), `test_reload_*`(TRUNCATE로 tastings 삭제/ids 변경)

- [ ] **Step 5: `pipeline/load.py` 전체 교체**

```python
from pathlib import Path

from psycopg.types.json import Jsonb

from pipeline.enrich import read_json_lines
from pipeline.query import to_vector_literal
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, read_jsonl

# Rows a user's tasting points at are never deleted, even if they vanish from the source.
PROTECTED_COFFEES = ("SELECT coffee_id FROM tastings WHERE coffee_id IS NOT NULL "
                     "UNION SELECT default_bean_coffee_id FROM brands WHERE default_bean_coffee_id IS NOT NULL "
                     "UNION SELECT decaf_bean_coffee_id FROM brands WHERE decaf_bean_coffee_id IS NOT NULL")
PROTECTED_MENU_ITEMS = "SELECT menu_item_id FROM tastings WHERE menu_item_id IS NOT NULL"
PROTECTED_BRANDS = f"SELECT brand_id FROM menu_items WHERE id IN ({PROTECTED_MENU_ITEMS})"


def _read_lines(path: Path) -> list[dict]:
    return read_json_lines(path)[0]


def _ids(conn, table: str) -> dict[str, int]:
    return dict(conn.execute(f"SELECT key, id FROM {table}").fetchall())


def _upsert(cur, table: str, cols: list[str], rows: list[tuple], casts: dict[str, str] | None = None) -> None:
    if not rows:
        return
    casts = casts or {}
    values = ", ".join(f"%s{casts.get(c, '')}" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "key")
    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({values}) "
                    f"ON CONFLICT (key) DO UPDATE SET {updates}", rows)


def _delete_missing(cur, table: str, keys: list[str], protected_ids_sql: str | None = None) -> int:
    sql = f"DELETE FROM {table} WHERE NOT (key = ANY(%s))"
    if protected_ids_sql:
        sql += f" AND id NOT IN ({protected_ids_sql})"
    cur.execute(sql, (keys,))
    return cur.rowcount


def run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path) -> dict[str, int]:
    """Upsert the knowledge tables by key in one transaction (a failed load rolls back).

    User tables are never touched; rows that vanished from the source are deleted unless a tasting
    (or a brand bean) still references them.
    """
    cur = conn.cursor()

    taxonomy = sorted(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode), key=lambda t: t.level)
    for t in taxonomy:
        cur.execute(
            "INSERT INTO flavor_taxonomy (key, parent_id, level, name_en, name_ko) "
            "VALUES (%s, (SELECT id FROM flavor_taxonomy WHERE key = %s), %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET parent_id = EXCLUDED.parent_id, level = EXCLUDED.level, "
            "name_en = EXCLUDED.name_en, name_ko = EXCLUDED.name_ko",
            (t.key, t.parent_key, t.level, t.name_en, t.name_ko))

    vectors = {e["key"]: e["vector"] for e in _read_lines(embedded_dir / "embeddings.jsonl")}
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    _upsert(cur, "coffees",
            ["key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level", "is_decaf",
             "decaf_process", "acidity", "body", "sweetness", "flavor_tags", "flavor_summary", "embedding",
             "source", "source_url", "collected_at"],
            [(c.key, c.name, c.roaster, c.origin_country, c.origin_region, c.process, c.roast_level, c.is_decaf,
              c.decaf_process, c.acidity, c.body, c.sweetness, c.flavor_tags, c.flavor_summary,
              to_vector_literal(vectors[c.key]) if c.key in vectors else None, c.source, c.source_url,
              c.collected_at) for c in coffees],
            casts={"embedding": "::vector"})
    coffee_ids = _ids(conn, "coffees")

    all_reviews = read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord)
    reviews = [r for r in all_reviews if r.coffee_key in coffee_ids]
    _upsert(cur, "reviews", ["key", "coffee_id", "text", "rating", "sub_scores", "source", "source_url", "collected_at"],
            [(r.key, coffee_ids[r.coffee_key], r.text, r.rating, Jsonb(r.sub_scores), r.source, r.source_url,
              r.collected_at) for r in reviews])

    brands = read_jsonl(norm_dir / "brands.jsonl", BrandRecord)
    _upsert(cur, "brands", ["key", "name", "decaf_available", "decaf_surcharge_krw", "default_bean_coffee_id",
                            "decaf_bean_coffee_id", "notes", "source_url", "verified_at"],
            [(b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, coffee_ids.get(b.default_bean_coffee_key),
              coffee_ids.get(b.decaf_bean_coffee_key), b.notes, b.source_url, b.verified_at) for b in brands])
    brand_ids = _ids(conn, "brands")

    all_items = read_jsonl(norm_dir / "menu_items.jsonl", MenuItemRecord)
    items = [m for m in all_items if m.brand_key in brand_ids]
    _upsert(cur, "menu_items", ["key", "brand_id", "name", "name_en", "category", "is_decaf", "decaf_option",
                                "caffeine_mg", "coffee_id", "source_url", "collected_at"],
            [(m.key, brand_ids[m.brand_key], m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,
              m.caffeine_mg, coffee_ids.get(m.coffee_key), m.source_url, m.collected_at) for m in items])

    deleted_reviews = _delete_missing(cur, "reviews", [r.key for r in reviews])
    deleted_menu = _delete_missing(cur, "menu_items", [m.key for m in items], PROTECTED_MENU_ITEMS)
    deleted_brands = _delete_missing(cur, "brands", [b.key for b in brands], PROTECTED_BRANDS)
    source_keys = [c.key for c in coffees]
    stale = cur.execute("SELECT count(*) FROM coffees WHERE NOT (key = ANY(%s))", (source_keys,)).fetchone()[0]
    deleted_coffees = _delete_missing(cur, "coffees", source_keys, PROTECTED_COFFEES)

    log = {e["key"]: e for e in _read_lines(enriched_dir / "cache.jsonl")}
    cur.execute("DELETE FROM enrich_log")
    cur.executemany(
        "INSERT INTO enrich_log (row_ref, stage, status, error, model) VALUES (%s, 'enrich', %s, %s, %s)",
        [(k, e["status"], e.get("error"), e.get("model")) for k, e in log.items()])

    conn.commit()
    return {"coffees": len(coffees), "reviews": len(reviews), "brands": len(brands), "menu_items": len(items),
            "flavor_taxonomy": len(taxonomy), "enrich_log": len(log),
            "dropped_reviews": len(all_reviews) - len(reviews), "dropped_menu_items": len(all_items) - len(items),
            "deleted_coffees": deleted_coffees, "deleted_reviews": deleted_reviews,
            "deleted_menu_items": deleted_menu, "deleted_brands": deleted_brands,
            "kept_referenced_coffees": stale - deleted_coffees}
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_db_load.py -v && uv run pytest -q`
Expected: 전부 PASS (db 테스트 skip 없음). 기존 `test_failed_load_keeps_previous_data`도 통과해야 한다(트랜잭션 롤백).

- [ ] **Step 7: 실데이터 재적재 확인**

Run: `uv run python -m pipeline run --only load`
Expected: `[load]`에 `deleted_* 0`, `coffees 9048`. 두 번 실행해도 `SELECT max(id) FROM coffees` 값이 그대로다.

- [ ] **Step 8: Commit**

```bash
git add db/schema.sql pipeline/db.py pipeline/load.py tests/test_db_load.py
git commit -m "feat(db): 사용자 테이블 추가, 적재를 key upsert로 전환(사용자 기록 보존)"
```

---

### Task 2: 브랜드 원두 속성

**Files:**
- Modify: `pipeline/records.py`, `data/curated/brands.yaml`, `db/schema.sql`, `pipeline/load.py`
- Test: `tests/test_normalize_menus.py`, `tests/test_db_load.py`

**Interfaces:**
- Produces: `pipeline.records.BeanProfile(acidity: float, body: float, sweetness: float, flavor_tags: list[str])` (각 1~5); `BrandRecord.bean: BeanProfile | None`, `BrandRecord.decaf_bean: BeanProfile | None`; DB `brands.bean jsonb`, `brands.decaf_bean jsonb` (BeanProfile.model_dump()).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_normalize_menus.py` 끝에:
```python
SCA_TAGS_USED = {"chocolate", "dark chocolate", "cocoa", "nutty", "almonds", "hazelnut", "caramelized",
                 "brown sugar", "vanilla", "honey", "citrus fruit", "lemon", "lime", "orange", "berry",
                 "floral", "black tea", "brown roast", "smoky"}


def test_every_brand_has_bean_profiles():
    from pipeline import settings
    brands = normalize_brands(settings.CURATED_DIR)
    assert len(brands) == 10
    for b in brands:
        assert b.bean is not None, b.key
        assert set(b.bean.flavor_tags) <= SCA_TAGS_USED, b.key
        if b.decaf_available:
            assert b.decaf_bean is not None, b.key
            assert set(b.decaf_bean.flavor_tags) <= SCA_TAGS_USED, b.key


def test_bean_profile_range_is_validated():
    import pytest
    from pydantic import ValidationError
    from pipeline.records import BeanProfile
    with pytest.raises(ValidationError):
        BeanProfile(acidity=6, body=3, sweetness=3, flavor_tags=[])
```
`tests/test_db_load.py` 끝에:
```python
def test_brand_bean_is_loaded(db_conn, tmp_path):
    from pipeline.records import BeanProfile
    norm, enriched, embedded = setup_files(tmp_path)
    write_jsonl(norm / "brands.jsonl", [BrandRecord(
        key="brand:x", name="X", decaf_available=True, verified_at="2026-09-24",
        bean=BeanProfile(acidity=2, body=4, sweetness=3, flavor_tags=["nutty"]),
        decaf_bean=BeanProfile(acidity=2, body=3, sweetness=3, flavor_tags=["chocolate"]))])
    run_load(db_conn, norm, enriched, embedded)
    bean, decaf = db_conn.execute("SELECT bean, decaf_bean FROM brands WHERE key = 'brand:x'").fetchone()
    assert bean == {"acidity": 2.0, "body": 4.0, "sweetness": 3.0, "flavor_tags": ["nutty"]}
    assert decaf["flavor_tags"] == ["chocolate"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_normalize_menus.py tests/test_db_load.py -v`
Expected: FAIL — `BeanProfile` import 불가 / `bean` 속성 없음

- [ ] **Step 3: `pipeline/records.py`** — `BrandRecord` 위에 추가하고 `BrandRecord`에 필드 2개 추가

```python
class BeanProfile(BaseModel):
    """Hand-curated taste of a brand's house bean (estimated from brand notes)."""
    acidity: float = Field(ge=1, le=5)
    body: float = Field(ge=1, le=5)
    sweetness: float = Field(ge=1, le=5)
    flavor_tags: list[str] = Field(default_factory=list)
```
`BrandRecord` 끝에:
```python
    bean: BeanProfile | None = None
    decaf_bean: BeanProfile | None = None
```

- [ ] **Step 4: `db/schema.sql` 끝에**

```sql
ALTER TABLE brands ADD COLUMN IF NOT EXISTS bean jsonb;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS decaf_bean jsonb;
```

- [ ] **Step 5: `pipeline/load.py`** — brands upsert를 다음으로 교체

```python
    brands = read_jsonl(norm_dir / "brands.jsonl", BrandRecord)
    _upsert(cur, "brands", ["key", "name", "decaf_available", "decaf_surcharge_krw", "default_bean_coffee_id",
                            "decaf_bean_coffee_id", "notes", "source_url", "verified_at", "bean", "decaf_bean"],
            [(b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, coffee_ids.get(b.default_bean_coffee_key),
              coffee_ids.get(b.decaf_bean_coffee_key), b.notes, b.source_url, b.verified_at,
              Jsonb(b.bean.model_dump()) if b.bean else None,
              Jsonb(b.decaf_bean.model_dump()) if b.decaf_bean else None) for b in brands])
```

- [ ] **Step 6: `data/curated/brands.yaml`** — 각 브랜드 항목의 `verified_at` 다음 줄에 추가. 값은 notes의 공식·뉴스 설명에서 추정한 것이며 파일 상단 주석에 그 사실을 적는다.

파일 첫 줄 주석 아래에 한 줄 추가:
```yaml
# bean / decaf_bean: notes의 원두 설명(산미·바디·향미 문구)에서 추정한 1~5 값. 실측 아님 — 추천 신뢰도는 medium으로 표시된다.
```
브랜드별 추가 내용:
```yaml
# brand:starbucks
  bean: {acidity: 2, body: 4, sweetness: 2, flavor_tags: [dark chocolate, caramelized]}
  decaf_bean: {acidity: 2, body: 3, sweetness: 2, flavor_tags: [chocolate, caramelized]}
# brand:twosome
  bean: {acidity: 2, body: 4, sweetness: 2, flavor_tags: [dark chocolate, nutty]}
  decaf_bean: {acidity: 2, body: 3, sweetness: 3, flavor_tags: [chocolate, nutty]}
# brand:mega
  bean: {acidity: 1, body: 4, sweetness: 2, flavor_tags: [nutty, dark chocolate]}
  decaf_bean: {acidity: 1, body: 3, sweetness: 2, flavor_tags: [nutty, chocolate]}
# brand:compose
  bean: {acidity: 1, body: 4, sweetness: 2, flavor_tags: [nutty, dark chocolate]}
  decaf_bean: {acidity: 2, body: 4, sweetness: 2, flavor_tags: [nutty, dark chocolate]}
# brand:paik
  bean: {acidity: 1, body: 4, sweetness: 2, flavor_tags: [nutty, chocolate]}
  decaf_bean: {acidity: 2, body: 3, sweetness: 2, flavor_tags: [chocolate]}
# brand:ediya
  bean: {acidity: 2, body: 3, sweetness: 2, flavor_tags: [chocolate, nutty]}
  decaf_bean: {acidity: 2, body: 3, sweetness: 3, flavor_tags: [chocolate, caramelized]}
# brand:hollys
  bean: {acidity: 2, body: 3, sweetness: 2, flavor_tags: [chocolate, nutty]}
  decaf_bean: {acidity: 2, body: 2, sweetness: 3, flavor_tags: [brown sugar]}
# brand:paulbassett
  bean: {acidity: 4, body: 3, sweetness: 3, flavor_tags: [chocolate, citrus fruit]}
  decaf_bean: {acidity: 3, body: 3, sweetness: 3, flavor_tags: [chocolate, citrus fruit]}
# brand:bluebottle
  bean: {acidity: 3, body: 3, sweetness: 3, flavor_tags: [chocolate, berry]}
  decaf_bean: {acidity: 3, body: 3, sweetness: 4, flavor_tags: [vanilla, caramelized, lime]}
# brand:coffeebean
  bean: {acidity: 2, body: 4, sweetness: 2, flavor_tags: [dark chocolate, caramelized]}
  decaf_bean: {acidity: 2, body: 3, sweetness: 2, flavor_tags: [dark chocolate]}
```
(`# brand:...` 줄은 어느 항목에 넣는지 표시일 뿐, 파일에 쓰지 않는다. 들여쓰기는 각 항목의 다른 필드와 같은 2칸.)

- [ ] **Step 7: 통과 확인 + 실데이터 반영**

Run: `uv run pytest -q && uv run python -m pipeline run --only normalize --only load`
Expected: 전부 PASS, `[load] brands 10`.

- [ ] **Step 8: Commit**

```bash
git add pipeline/records.py pipeline/load.py db/schema.sql data/curated/brands.yaml tests/test_normalize_menus.py tests/test_db_load.py
git commit -m "feat(data): 브랜드 일반·디카페인 원두 취향 속성(수기 추정) 추가"
```

---

### Task 3: 앱 골격 — 의존성, 설정, 모델, 트레이싱

**Files:**
- Modify: `pyproject.toml`
- Create: `app/__init__.py`(빈 파일), `app/config.py`, `app/models.py`, `app/tracing.py`, `app/core/__init__.py`(빈), `app/graphs/__init__.py`(Task 9에서 채움 — 여기선 빈 파일), `tests/app/__init__.py`(빈), `tests/app/test_models.py`

**Interfaces:**
- Produces:
  - `app.config`: `COOKIE_NAME="cs_uid"`, `COOKIE_MAX_AGE`, `EXPLAIN_TASK="explain"`, `PARSE_NOTE_TASK="parse_note"`, `PARSE_BEAN_TASK="parse_bean"`, `cookie_secure() -> bool`, `cookie_samesite() -> str`, `allowed_origins() -> list[str]`
  - `app.models`: `ATTRS=("acidity","body","sweetness")`, `CAFFEINE_RULES`, `Item`(frozen dataclass), `Profile`(dataclass, `to_dict()`, `from_dict()`), `Neighbor`, `Prediction`, `ParsedBean`
  - `app.tracing`: `enabled() -> bool`, `traced(name) -> decorator`, `span(name) -> context manager`, `flush()`

- [ ] **Step 1: 의존성 추가** — `pyproject.toml`의 `dependencies`에

```toml
  "langgraph>=1.2,<2",
  "fastapi>=0.140",
  "uvicorn[standard]>=0.30",
  "psycopg-pool>=3.2",
  "langfuse>=4.15,<5",
```
Run: `uv sync` → Expected: 설치 성공. `uv run python -c "import langgraph, fastapi, psycopg_pool, langfuse"` 오류 없음.

- [ ] **Step 2: 실패하는 테스트** — `tests/app/test_models.py`

```python
from app import tracing
from app.models import Item, Profile


def test_profile_roundtrip_ignores_unknown_keys():
    p = Profile(caffeine_rule="decaf_only", milk_ok=False, acidity=4.5, flavor_weights={"fruity": 0.5}, n_updates=2)
    d = p.to_dict() | {"unknown": 1}
    assert Profile.from_dict(d) == p


def test_item_attr_access():
    it = Item(key="coffee:1", name="x", source="db", acidity=4, tags=("lemon",))
    assert it.attr("acidity") == 4 and it.attr("body") is None


def test_tracing_is_noop_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    def f(x):
        return x

    assert tracing.enabled() is False
    assert tracing.traced("n")(f) is f
    with tracing.span("run"):
        pass
```

- [ ] **Step 3: 실패 확인** — Run: `uv run pytest tests/app/test_models.py -v` → Expected: FAIL `No module named 'app'`

- [ ] **Step 4: 구현**

`app/config.py`:
```python
import os

from pipeline import settings  # noqa: F401  (loads .env)

COOKIE_NAME = "cs_uid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
EXPLAIN_TASK = "explain"
PARSE_NOTE_TASK = "parse_note"
PARSE_BEAN_TASK = "parse_bean"


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "true").lower() == "true"


def cookie_samesite() -> str:
    return os.getenv("COOKIE_SAMESITE", "lax").lower()


def allowed_origins() -> list[str]:
    return [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
```

`app/models.py`:
```python
from dataclasses import asdict, dataclass, field

ATTRS = ("acidity", "body", "sweetness")
CAFFEINE_RULES = ("decaf_only", "low", "any")


@dataclass(frozen=True)
class Item:
    """Anything that can be recommended or logged: a DB coffee, a menu drink, or a predicted bean."""
    key: str                      # "coffee:12" | "menu:34" | "brand:twosome:아메리카노" | "input"
    name: str
    source: str                   # "db" | "brand_bean" | "predicted"
    acidity: float | None = None
    body: float | None = None
    sweetness: float | None = None
    tags: tuple[str, ...] = ()
    is_decaf: bool = False
    decaf_option: bool = False
    order_decaf: bool = False     # recommend ordering the decaf version of this drink
    caffeine_mg: float | None = None
    is_milk: bool = False
    confidence: str = "high"      # high | medium | low
    brand: str | None = None
    decaf_surcharge_krw: int | None = None
    coffee_id: int | None = None
    menu_item_id: int | None = None
    origin_country: str | None = None
    process: str | None = None

    def attr(self, name: str) -> float | None:
        return getattr(self, name)


@dataclass
class Profile:
    caffeine_rule: str = "any"
    milk_ok: bool = True
    acidity: float = 3.0
    body: float = 3.0
    sweetness: float = 3.0
    flavor_weights: dict[str, float] = field(default_factory=dict)   # SCA category -> [-1, 1]
    n_updates: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class Neighbor:
    coffee_id: int
    name: str
    similarity: float
    acidity: float | None
    body: float | None
    sweetness: float | None
    tags: tuple[str, ...] = ()


@dataclass
class Prediction:
    acidity: float | None
    body: float | None
    sweetness: float | None
    confidence: str
    tags: list[str]
    evidence: list[str]
    n_neighbors: int


@dataclass(frozen=True)
class ParsedBean:
    text: str
    origin_country: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool = False
    decaf_process: str | None = None
```

`app/tracing.py`:
```python
"""Langfuse tracing that disappears entirely when keys are not configured."""
import contextlib
import os


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def traced(name: str):
    if not enabled():
        return lambda f: f
    from langfuse import observe
    return observe(name=name)


def span(name: str):
    if not enabled():
        return contextlib.nullcontext()
    from langfuse import get_client
    return get_client().start_as_current_observation(name=name, as_type="span")


def flush() -> None:
    if enabled():
        from langfuse import get_client
        get_client().flush()
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/app/test_models.py -v && uv run pytest -q` → Expected: 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock app tests/app
git commit -m "feat(app): 2단계 앱 골격 — 의존성(LangGraph·FastAPI·Langfuse), 설정, 도메인 모델, 트레이싱"
```

---

### Task 4: 향미 카테고리 + 점수·필터·MMR

**Files:**
- Create: `app/core/flavors.py`, `app/core/scoring.py`, `tests/app/test_scoring.py`

**Interfaces:**
- Consumes: `app.models.Item`, `Profile`, `ATTRS`
- Produces:
  - `flavors`: `CATEGORIES`, `CATEGORY_KO`, `PREFERENCE_CHIPS`, `build_tag_to_category(rows: Iterable[tuple[str, int, str]]) -> dict[str, str]`, `category_vector(tags, tag_to_cat) -> dict[str, float]`, `cosine(a: dict, b: dict) -> float | None`
  - `scoring`: `LOW_CAFFEINE_MG=100`, `is_milk_drink(name) -> bool`, `passes(profile, item) -> tuple[bool, str | None]`, `attr_fit(profile, item) -> float | None`, `flavor_fit(profile, item, tag_to_cat) -> float | None`, `score_item(profile, item, tag_to_cat) -> float` (0~1), `mmr_top_k(scored: list[tuple[Item, float]], tag_to_cat, k=3, lam=0.7) -> list[tuple[Item, float]]`

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_scoring.py`

```python
import pytest

from app.core.flavors import build_tag_to_category, category_vector, cosine
from app.core.scoring import attr_fit, flavor_fit, is_milk_drink, mmr_top_k, passes, score_item
from app.models import Item, Profile

T2C = {"lemon": "fruity", "jasmine": "floral", "chocolate": "nutty/cocoa", "nutty": "nutty/cocoa"}


def item(**kw):
    base = dict(key="coffee:1", name="x", source="db")
    return Item(**(base | kw))


def test_build_tag_to_category_uses_level_one_ancestor():
    rows = [("sca:fruity", 1, "fruity"), ("sca:fruity>citrus fruit", 2, "citrus fruit"),
            ("sca:fruity>citrus fruit>lemon", 3, "Lemon")]
    assert build_tag_to_category(rows) == {"citrus fruit": "fruity", "lemon": "fruity"}


def test_category_vector_and_cosine():
    v = category_vector(["lemon", "jasmine", "unknown"], T2C)
    assert v == {"fruity": 0.5, "floral": 0.5}
    assert cosine({"fruity": 1}, {"floral": 1}) == 0
    assert cosine({}, {"fruity": 1}) is None


@pytest.mark.parametrize("name,expected", [("카페 라떼", True), ("Iced Mocha", True), ("아메리카노", False),
                                           ("디카페인 콜드브루", False), ("바닐라 크림 콜드브루", True)])
def test_is_milk_drink(name, expected):
    assert is_milk_drink(name) is expected


def test_hard_filters():
    decaf_user = Profile(caffeine_rule="decaf_only")
    assert passes(decaf_user, item(is_decaf=False)) == (False, "디카페인이 아니에요")
    assert passes(decaf_user, item(decaf_option=True))[0] is True
    low = Profile(caffeine_rule="low")
    assert passes(low, item(caffeine_mg=80))[0] is True
    assert passes(low, item(caffeine_mg=150))[0] is False
    assert passes(low, item(caffeine_mg=None))[0] is False      # unknown caffeine is not "low"
    no_milk = Profile(milk_ok=False)
    assert passes(no_milk, item(is_milk=True)) == (False, "우유가 들어가요")


def test_attr_fit_skips_missing_and_halves_acidity_for_milk():
    p = Profile(acidity=5, body=3)
    assert attr_fit(p, item(acidity=5, body=3)) == 1.0
    assert attr_fit(p, item()) is None
    black = attr_fit(p, item(acidity=1, body=3))                 # (0 + 1) / 2
    milk = attr_fit(p, item(acidity=1, body=3, is_milk=True))   # (0.5*0 + 1) / 1.5
    assert black == pytest.approx(0.5) and milk == pytest.approx(2 / 3)


def test_flavor_fit_and_score_mix():
    p = Profile(acidity=4, body=3, sweetness=3, flavor_weights={"fruity": 1.0})
    it = item(acidity=4, body=3, sweetness=3, tags=("lemon",))
    assert flavor_fit(p, it, T2C) == pytest.approx(1.0)
    assert score_item(p, it, T2C) == pytest.approx(1.0)
    assert flavor_fit(Profile(), it, T2C) is None               # no preferences yet
    assert score_item(Profile(), item(), T2C) == 0.5            # nothing known


def test_mmr_prefers_diverse_second_pick():
    a = item(key="a", acidity=4, body=2, sweetness=3, tags=("lemon",))
    a2 = item(key="a2", acidity=4, body=2, sweetness=3, tags=("lemon",))     # near-duplicate of a
    b = item(key="b", acidity=2, body=4, sweetness=3, tags=("chocolate",))
    top = mmr_top_k([(a, 0.90), (a2, 0.89), (b, 0.80)], T2C, k=2)
    assert [i.key for i, _ in top] == ["a", "b"]
    assert mmr_top_k([], T2C) == []
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_scoring.py -v` → Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현**

`app/core/flavors.py`:
```python
from math import sqrt
from typing import Iterable

CATEGORIES = ("fruity", "floral", "sweet", "nutty/cocoa", "roasted", "spices", "sour/fermented",
              "green/vegetative", "other")
CATEGORY_KO = {"fruity": "과일", "floral": "꽃", "sweet": "단맛", "nutty/cocoa": "견과/코코아", "roasted": "로스팅",
               "spices": "향신료", "sour/fermented": "신맛/발효", "green/vegetative": "풀/채소", "other": "기타"}
# Categories offered as "I like this" chips during onboarding (the last two are defects, not preferences).
PREFERENCE_CHIPS = ("fruity", "floral", "sweet", "nutty/cocoa", "roasted", "spices", "sour/fermented")


def build_tag_to_category(rows: Iterable[tuple[str, int, str]]) -> dict[str, str]:
    """rows: (taxonomy key like 'sca:fruity>berry>blackberry', level, name_en) -> tag -> level-1 category."""
    out: dict[str, str] = {}
    for key, level, name in rows:
        if level < 2:
            continue
        out.setdefault(name.lower(), key.removeprefix("sca:").split(">")[0])
    return out


def category_vector(tags: Iterable[str], tag_to_cat: dict[str, str]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for t in tags:
        c = tag_to_cat.get(t.lower())
        if c:
            counts[c] = counts.get(c, 0) + 1
    total = sum(counts.values())
    return {c: n / total for c, n in counts.items()} if total else {}


def cosine(a: dict[str, float], b: dict[str, float]) -> float | None:
    na = sqrt(sum(v * v for v in a.values()))
    nb = sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return None
    return sum(a[k] * b[k] for k in a.keys() & b.keys()) / (na * nb)
```

`app/core/scoring.py`:
```python
from app.core.flavors import category_vector, cosine
from app.models import ATTRS, Item, Profile

LOW_CAFFEINE_MG = 100
ATTR_WEIGHT, FLAVOR_WEIGHT = 0.6, 0.4
MILK_WORDS = ("라떼", "우유", "밀크", "크림", "카푸치노", "플랫화이트", "모카", "프라푸치노",
              "latte", "milk", "cream", "cappuccino", "flat white", "mocha", "frappuccino")


def is_milk_drink(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in MILK_WORDS)


def passes(profile: Profile, item: Item) -> tuple[bool, str | None]:
    decaf_ok = item.is_decaf or item.decaf_option
    if profile.caffeine_rule == "decaf_only" and not decaf_ok:
        return False, "디카페인이 아니에요"
    if profile.caffeine_rule == "low" and not (
            decaf_ok or (item.caffeine_mg is not None and item.caffeine_mg <= LOW_CAFFEINE_MG)):
        return False, "카페인이 100mg을 넘거나 알 수 없어요"
    if not profile.milk_ok and item.is_milk:
        return False, "우유가 들어가요"
    return True, None


def attr_fit(profile: Profile, item: Item) -> float | None:
    num = den = 0.0
    for a in ATTRS:
        v = item.attr(a)
        if v is None:
            continue
        w = 0.5 if (a == "acidity" and item.is_milk) else 1.0   # milk mutes acidity
        num += w * (1 - abs(getattr(profile, a) - v) / 4)
        den += w
    return num / den if den else None


def flavor_fit(profile: Profile, item: Item, tag_to_cat: dict[str, str]) -> float | None:
    c = cosine(profile.flavor_weights, category_vector(item.tags, tag_to_cat))
    return None if c is None else (c + 1) / 2


def score_item(profile: Profile, item: Item, tag_to_cat: dict[str, str]) -> float:
    a, f = attr_fit(profile, item), flavor_fit(profile, item, tag_to_cat)
    if a is None and f is None:
        return 0.5
    if a is None:
        return f
    if f is None:
        return a
    return ATTR_WEIGHT * a + FLAVOR_WEIGHT * f


def _features(item: Item, tag_to_cat: dict[str, str]) -> dict[str, float]:
    feats = {a: (item.attr(a) if item.attr(a) is not None else 3.0) / 5 for a in ATTRS}
    feats.update({f"cat:{c}": v for c, v in category_vector(item.tags, tag_to_cat).items()})
    return feats


def mmr_top_k(scored: list[tuple[Item, float]], tag_to_cat: dict[str, str], k: int = 3,
              lam: float = 0.7) -> list[tuple[Item, float]]:
    pool = sorted(scored, key=lambda x: -x[1])
    feats = {it.key: _features(it, tag_to_cat) for it, _ in pool}
    chosen: list[tuple[Item, float]] = []
    while pool and len(chosen) < k:
        def mmr(candidate: tuple[Item, float]) -> float:
            sim = max((cosine(feats[candidate[0].key], feats[c.key]) or 0.0 for c, _ in chosen), default=0.0)
            return lam * candidate[1] - (1 - lam) * sim
        best = max(pool, key=mmr)
        chosen.append(best)
        pool.remove(best)
    return chosen
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_scoring.py -v` → Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/flavors.py app/core/scoring.py tests/app/test_scoring.py
git commit -m "feat(core): 향미 카테고리, 하드 조건 필터, 취향 적합도, MMR 다양성 top-k"
```

---

### Task 5: 예측(RAG 집계) + 프로필 학습 + 수렴 시뮬레이션

**Files:**
- Create: `app/core/predict.py`, `app/core/learning.py`, `app/core/simulate.py`, `tests/app/test_predict_learning.py`

**Interfaces:**
- Consumes: `app.models`, `app.core.flavors`, `app.core.scoring.score_item`
- Produces:
  - `predict`: `MIN_NEIGHBORS=3`, `predict_from_neighbors(neighbors: list[Neighbor], tag_ko: dict[str,str] | None = None) -> Prediction`, `item_from_prediction(parsed: ParsedBean, pred: Prediction) -> Item` (key `"input"`, source `"predicted"`)
  - `learning`: `update_profile(profile, item, rating: int, tag_to_cat, signals: dict | None = None) -> tuple[Profile, list[str]]` (원본 불변, 변경 설명 목록)
  - `simulate`: `simulate_convergence(items: list[Item], tag_to_cat, users=100, steps=10, seed=1, explore=0.3, candidates=20) -> dict` (`{"users","steps","mae_by_step": list[float], "improvement": float}`)

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_predict_learning.py`

```python
import itertools

import pytest

from app.core.learning import update_profile
from app.core.predict import item_from_prediction, predict_from_neighbors
from app.core.simulate import simulate_convergence
from app.models import Item, Neighbor, ParsedBean, Profile

T2C = {"lemon": "fruity", "floral": "floral", "cocoa": "nutty/cocoa"}


def nb(i, sim, acidity, body, tags):
    return Neighbor(coffee_id=i, name=f"n{i}", similarity=sim, acidity=acidity, body=body, sweetness=None, tags=tags)


def test_prediction_weighted_mean_confidence_tags_and_evidence():
    neigh = [nb(1, 0.9, 4, 2, ("lemon",)), nb(2, 0.8, 4, 2, ("lemon", "floral")),
             nb(3, 0.7, 5, 2, ("cocoa",)), nb(4, 0.6, 4, 3, ("cocoa", "lemon"))]
    p = predict_from_neighbors(neigh, {"lemon": "레몬"})
    assert p.acidity == pytest.approx(4.23, abs=0.01)
    assert p.body == pytest.approx(2.2, abs=0.01)
    assert p.sweetness is None                      # no neighbor has sweetness
    assert p.confidence == "high"                   # stds ~0.42 / 0.40
    assert p.tags == ["lemon", "cocoa"]             # floral share 0.27 < 0.3
    assert p.evidence[0] == "유사 원두 4개 중 3개에서 '레몬' 언급"
    assert p.evidence[1] == "유사 원두 4개 중 2개에서 'cocoa' 언급"
    assert p.n_neighbors == 4


def test_too_few_neighbors_is_low_confidence():
    p = predict_from_neighbors([nb(1, 0.9, 4, 2, ()), nb(2, 0.8, 1, 5, ())])
    assert (p.acidity, p.confidence, p.tags) == (None, "low", [])
    assert predict_from_neighbors([]).confidence == "low"


def test_item_from_prediction():
    parsed = ParsedBean(text="예가체프 디카페인", origin_country="Ethiopia", is_decaf=True)
    pred = predict_from_neighbors([nb(i, 0.9, 4, 2, ("lemon",)) for i in range(3)])
    it = item_from_prediction(parsed, pred)
    assert (it.key, it.source, it.name, it.is_decaf, it.acidity, it.tags) == (
        "input", "predicted", "예가체프 디카페인", True, 4.0, ("lemon",))


def item(**kw):
    return Item(**({"key": "coffee:1", "name": "x", "source": "db"} | kw))


def test_like_pulls_preference_toward_item():
    new, changes = update_profile(Profile(acidity=3), item(acidity=5), 5, T2C)
    assert new.acidity == pytest.approx(4.0)       # eta 1/2 * s 1 * d 2
    assert new.body == 3.0 and new.n_updates == 1
    assert "산미 선호 3.0→4.0" in changes


def test_dislike_pushes_only_when_close():
    near, _ = update_profile(Profile(acidity=3), item(acidity=4), 1, T2C)
    assert near.acidity == pytest.approx(2.75)
    far, _ = update_profile(Profile(acidity=2), item(acidity=5), 1, T2C)
    assert far.acidity == 2.0


def test_signals_and_clamp_and_flavors():
    p = Profile(acidity=3, sweetness=4.8)
    new, _ = update_profile(p, item(), 3, T2C, {"acidity": "lower", "sweetness": "higher",
                                                  "liked_flavors": ["floral", "bogus"]})
    assert (new.acidity, new.sweetness) == (2.5, 5.0)
    assert new.flavor_weights == {"floral": 0.3}
    liked, _ = update_profile(Profile(), item(tags=("lemon",)), 5, T2C)
    assert liked.flavor_weights == {"fruity": 0.5}
    assert p.acidity == 3                          # input profile untouched


def test_simulated_users_converge():
    grid = [Item(key=f"g{i}", name="g", source="db", acidity=a, body=b, sweetness=s)
            for i, (a, b, s) in enumerate(itertools.product(range(1, 6), repeat=3))]
    r = simulate_convergence(grid, {}, users=100, steps=10, seed=1)
    assert len(r["mae_by_step"]) == 11
    assert r["mae_by_step"][-1] < r["mae_by_step"][0] - 0.05
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_predict_learning.py -v` → Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현**

`app/core/predict.py`:
```python
from math import sqrt

from app.models import ATTRS, Item, Neighbor, ParsedBean, Prediction

MIN_NEIGHBORS = 3
TAG_SHARE = 0.3
MAX_TAGS = 5
HIGH_STD, MEDIUM_STD = 0.7, 1.2


def _weighted(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    total = sum(w for _, w in pairs)
    mean = sum(v * w for v, w in pairs) / total
    var = sum(w * (v - mean) ** 2 for v, w in pairs) / total
    return mean, sqrt(var)


def predict_from_neighbors(neighbors: list[Neighbor], tag_ko: dict[str, str] | None = None) -> Prediction:
    tag_ko = tag_ko or {}
    weighted = [(n, max(n.similarity, 0.01)) for n in neighbors]
    values: dict[str, float | None] = {}
    stds: list[float] = []
    for a in ATTRS:
        pairs = [(getattr(n, a), w) for n, w in weighted if getattr(n, a) is not None]
        if len(pairs) < MIN_NEIGHBORS:
            values[a] = None
            continue
        mean, std = _weighted(pairs)
        values[a] = round(mean, 2)
        stds.append(std)
    if len(neighbors) < MIN_NEIGHBORS or not stds:
        confidence = "low"
    else:
        mean_std = sum(stds) / len(stds)
        confidence = "high" if mean_std <= HIGH_STD else "medium" if mean_std <= MEDIUM_STD else "low"

    total = sum(w for _, w in weighted) or 1.0
    weight_by_tag: dict[str, float] = {}
    count_by_tag: dict[str, int] = {}
    for n, w in weighted:
        for t in {x.lower() for x in n.tags}:
            weight_by_tag[t] = weight_by_tag.get(t, 0.0) + w
            count_by_tag[t] = count_by_tag.get(t, 0) + 1
    ranked = sorted(weight_by_tag.items(), key=lambda kv: (-kv[1], kv[0]))
    tags = [t for t, w in ranked if w / total >= TAG_SHARE][:MAX_TAGS]

    evidence = [f"유사 원두 {len(neighbors)}개 중 {count_by_tag[t]}개에서 '{tag_ko.get(t, t)}' 언급" for t in tags[:2]]
    if values["acidity"] is not None:
        evidence.append(f"유사 원두 산미 평균 {values['acidity']:.1f}/5")
    return Prediction(acidity=values["acidity"], body=values["body"], sweetness=values["sweetness"],
                      confidence=confidence, tags=tags, evidence=evidence, n_neighbors=len(neighbors))


def item_from_prediction(parsed: ParsedBean, pred: Prediction) -> Item:
    return Item(key="input", name=parsed.text, source="predicted", acidity=pred.acidity, body=pred.body,
                sweetness=pred.sweetness, tags=tuple(pred.tags), is_decaf=parsed.is_decaf,
                confidence=pred.confidence, origin_country=parsed.origin_country, process=parsed.process)
```

`app/core/learning.py`:
```python
from dataclasses import replace

from app.core.flavors import CATEGORIES, CATEGORY_KO, category_vector
from app.models import ATTRS, Item, Profile

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}
SIGNAL_STEP = 0.5            # explicit "too acidic" etc. from the note
FLAVOR_SIGNAL_STEP = 0.3
PUSH_RANGE = 2.0             # a dislike only pushes when the item was this close to the preference
REPORT_THRESHOLD = 0.05


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def update_profile(profile: Profile, item: Item, rating: int, tag_to_cat: dict[str, str],
                   signals: dict | None = None) -> tuple[Profile, list[str]]:
    new = replace(profile, flavor_weights=dict(profile.flavor_weights))
    s = (rating - 3) / 2
    eta = 1 / (profile.n_updates + 2)

    for a in ATTRS:
        v = item.attr(a)
        if v is None or s == 0:
            continue
        pref = getattr(new, a)
        d = v - pref
        if s > 0:
            pref += eta * s * d
        elif d != 0 and abs(d) <= PUSH_RANGE:
            pref -= eta * abs(s) * (1 if d > 0 else -1) * (1 - abs(d) / PUSH_RANGE)
        setattr(new, a, _clamp(pref, 1, 5))

    if s != 0:
        for cat, share in category_vector(item.tags, tag_to_cat).items():
            new.flavor_weights[cat] = _clamp(new.flavor_weights.get(cat, 0.0) + eta * s * share, -1, 1)

    signals = signals or {}
    for a in ATTRS:
        direction = signals.get(a)
        if direction in ("lower", "higher"):
            step = -SIGNAL_STEP if direction == "lower" else SIGNAL_STEP
            setattr(new, a, _clamp(getattr(new, a) + step, 1, 5))
    for key, step in (("liked_flavors", FLAVOR_SIGNAL_STEP), ("disliked_flavors", -FLAVOR_SIGNAL_STEP)):
        for cat in signals.get(key) or []:
            if cat in CATEGORIES:
                new.flavor_weights[cat] = _clamp(new.flavor_weights.get(cat, 0.0) + step, -1, 1)

    new.n_updates = profile.n_updates + 1
    changes = [f"{ATTR_KO[a]} 선호 {getattr(profile, a):.1f}→{getattr(new, a):.1f}"
               for a in ATTRS if abs(getattr(new, a) - getattr(profile, a)) >= REPORT_THRESHOLD]
    for cat, w in new.flavor_weights.items():
        old = profile.flavor_weights.get(cat, 0.0)
        if abs(w - old) >= REPORT_THRESHOLD:
            changes.append(f"'{CATEGORY_KO.get(cat, cat)}' 선호 {'↑' if w > old else '↓'}")
    return new, changes
```

`app/core/simulate.py`:
```python
"""Simulated users with a hidden true taste: does the profile approach it as they log drinks?"""
import random

from app.core.flavors import PREFERENCE_CHIPS
from app.core.learning import update_profile
from app.core.scoring import score_item
from app.models import ATTRS, Item, Profile


def _mae(p: Profile, truth: Profile) -> float:
    return sum(abs(getattr(p, a) - getattr(truth, a)) for a in ATTRS) / len(ATTRS)


def _rating(truth: Profile, item: Item, tag_to_cat: dict[str, str], rng: random.Random) -> int:
    fit = score_item(truth, item, tag_to_cat)
    # People rate relative to what they usually drink: a fit of 0.55 is "meh", 0.95 is "love it".
    return max(1, min(5, round(1 + 4 * (fit - 0.55) / 0.4 + rng.gauss(0, 0.5))))


def simulate_convergence(items: list[Item], tag_to_cat: dict[str, str], users: int = 100, steps: int = 10,
                         seed: int = 1, explore: float = 0.3, candidates: int = 20) -> dict:
    rng = random.Random(seed)
    curves: list[list[float]] = []
    for _ in range(users):
        truth = Profile(**{a: rng.uniform(1.5, 4.5) for a in ATTRS},
                        flavor_weights={c: rng.uniform(-1, 1) for c in PREFERENCE_CHIPS})
        profile = Profile()
        curve = [_mae(profile, truth)]
        for _ in range(steps):
            pool = rng.sample(items, min(candidates, len(items)))
            if rng.random() < explore:
                chosen = rng.choice(pool)
            else:
                chosen = max(pool, key=lambda it: score_item(profile, it, tag_to_cat))
            profile, _ = update_profile(profile, chosen, _rating(truth, chosen, tag_to_cat, rng), tag_to_cat)
            curve.append(_mae(profile, truth))
        curves.append(curve)
    mae = [round(sum(c[i] for c in curves) / len(curves), 4) for i in range(steps + 1)]
    return {"users": users, "steps": steps, "mae_by_step": mae, "improvement": round(mae[0] - mae[-1], 4)}
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_predict_learning.py -v` → Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/predict.py app/core/learning.py app/core/simulate.py tests/app/test_predict_learning.py
git commit -m "feat(core): 유사 원두 기반 예측, 별점·후기 기반 프로필 학습, 수렴 시뮬레이션"
```

---

### Task 6: 원두 텍스트 파싱 + 설명 템플릿·LLM 메시지·카드

**Files:**
- Create: `app/core/parse.py`, `app/core/explain.py`, `tests/app/test_parse_explain.py`

**Interfaces:**
- Consumes: `pipeline.rules.detect_decaf/normalize_country/normalize_process/normalize_roast`, `app.models`, `app.core.flavors.CATEGORIES`
- Produces:
  - `parse`: `parse_bean_text(text) -> ParsedBean`, `needs_llm_parse(parsed) -> bool`, `BeanParse`(pydantic: origin_country, process, roast_level: str|None, is_decaf: bool|None), `merge_llm_parse(parsed, llm: BeanParse) -> ParsedBean`, `bean_parse_messages(text) -> list[dict]`, `NoteSignals`(pydantic), `note_messages(note) -> list[dict]`
  - `explain`: `CONFIDENCE_KO`, `template_explanation(item, profile, score, tag_ko, violation=None) -> str`, `explain_messages(item, profile, score, prediction=None) -> list[dict]`, `card(item, score, template, violation=None, prediction=None) -> dict`

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_parse_explain.py`

```python
import json

from app.core.explain import card, explain_messages, template_explanation
from app.core.parse import BeanParse, NoteSignals, merge_llm_parse, needs_llm_parse, parse_bean_text
from app.models import Item, Prediction, Profile


def test_parse_bean_text_rules():
    p = parse_bean_text("  에티오피아 예가체프 워시드 디카페인 스위스워터 ")
    assert (p.text, p.origin_country, p.process, p.is_decaf, p.decaf_process) == (
        "에티오피아 예가체프 워시드 디카페인 스위스워터", "Ethiopia", "washed", True, "unknown")
    assert needs_llm_parse(p) is False
    unknown = parse_bean_text("동네 로스터리 하우스 블렌드")
    assert unknown.origin_country is None and needs_llm_parse(unknown) is True


def test_merge_llm_parse_normalizes_and_keeps_rule_values():
    p = parse_bean_text("하우스 블렌드 내추럴")
    merged = merge_llm_parse(p, BeanParse(origin_country="브라질", process="washed", roast_level="Medium-Dark",
                                          is_decaf=True))
    assert merged.origin_country == "Brazil"
    assert merged.process == "natural"               # rule result wins over the LLM
    assert (merged.roast_level, merged.is_decaf) == ("medium-dark", True)


def test_note_signals_schema():
    s = NoteSignals.model_validate({"acidity": "lower", "liked_flavors": ["fruity"]})
    assert s.model_dump(exclude_none=True) == {"acidity": "lower", "liked_flavors": ["fruity"], "disliked_flavors": []}


def test_template_explanation():
    it = Item(key="menu:1", name="카페 아메리카노", source="brand_bean", acidity=4, body=3, tags=("lemon", "floral"),
              decaf_option=True, order_decaf=True, decaf_surcharge_krw=300)
    text = template_explanation(it, Profile(acidity=4, body=3), 0.87, {"lemon": "레몬"})
    assert text.startswith("취향 적합도 87%.")
    assert "산미, 바디이(가) 선호와 가까워요." in text
    assert "향미: 레몬, floral." in text
    assert "디카페인으로 변경해서 주문하세요 (+300원)." in text
    warn = template_explanation(it, Profile(), 0.5, {}, violation="우유가 들어가요")
    assert warn.startswith("주의: 우유가 들어가요.")


def test_explain_messages_carry_data_not_review_text():
    it = Item(key="input", name="예가체프", source="predicted", acidity=4.2, confidence="medium")
    pred = Prediction(acidity=4.2, body=None, sweetness=None, confidence="medium", tags=["lemon"],
                      evidence=["유사 원두 10개 중 8개에서 '레몬' 언급"], n_neighbors=10)
    msgs = explain_messages(it, Profile(flavor_weights={"fruity": 0.6}), 0.8, pred)
    assert msgs[0]["role"] == "system" and "2문장" in msgs[0]["content"]
    payload = json.loads(msgs[1]["content"])
    assert payload["근거"] == ["유사 원두 10개 중 8개에서 '레몬' 언급"]
    assert payload["손님 선호"]["좋아하는 향미"] == ["과일"]


def test_card_shape():
    it = Item(key="coffee:7", name="Kenya", source="db", acidity=4, coffee_id=7)
    c = card(it, 0.834, "t")
    assert c["score"] == 83 and c["key"] == "coffee:7" and c["coffee_id"] == 7 and c["template"] == "t"
    assert c["violation"] is None and "evidence" not in c
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_parse_explain.py -v` → Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현**

`app/core/parse.py`:
```python
from typing import Literal

from pydantic import BaseModel, Field

from app.models import ParsedBean
from pipeline.rules import detect_decaf, normalize_country, normalize_process, normalize_roast


def parse_bean_text(text: str) -> ParsedBean:
    t = " ".join((text or "").split())
    is_decaf, decaf_process = detect_decaf(t)
    return ParsedBean(text=t, origin_country=normalize_country(t), process=normalize_process(t),
                      roast_level=normalize_roast(t), is_decaf=is_decaf, decaf_process=decaf_process)


def needs_llm_parse(parsed: ParsedBean) -> bool:
    return bool(parsed.text) and parsed.origin_country is None


class BeanParse(BaseModel):
    origin_country: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool | None = None


def merge_llm_parse(parsed: ParsedBean, llm: BeanParse) -> ParsedBean:
    """Rule values win; the LLM only fills gaps, and its strings go through the same normalizers."""
    return ParsedBean(
        text=parsed.text,
        origin_country=parsed.origin_country or normalize_country(llm.origin_country),
        process=parsed.process or normalize_process(llm.process),
        roast_level=parsed.roast_level or normalize_roast(llm.roast_level),
        is_decaf=parsed.is_decaf or bool(llm.is_decaf),
        decaf_process=parsed.decaf_process or ("unknown" if llm.is_decaf else None),
    )


def bean_parse_messages(text: str) -> list[dict]:
    return [
        {"role": "system", "content": "You extract coffee bean facts from a café bean card. Reply with one JSON object only."},
        {"role": "user", "content": (
            f"Bean card text: {text}\n\nReturn JSON: {{\"origin_country\": English country name or null, "
            "\"process\": washed|natural|honey|anaerobic|null, \"roast_level\": light|medium|dark|null, "
            "\"is_decaf\": true|false|null}. Use null when the text does not say.")},
    ]


class NoteSignals(BaseModel):
    acidity: Literal["lower", "higher"] | None = None
    body: Literal["lower", "higher"] | None = None
    sweetness: Literal["lower", "higher"] | None = None
    liked_flavors: list[str] = Field(default_factory=list)
    disliked_flavors: list[str] = Field(default_factory=list)


def note_messages(note: str) -> list[dict]:
    return [
        {"role": "system", "content": "손님이 마신 커피에 남긴 한 줄 후기에서 취향 신호를 뽑는다. JSON 객체 하나만 답한다."},
        {"role": "user", "content": (
            f"후기: {note}\n\nJSON: {{\"acidity\": \"lower\"|\"higher\"|null, \"body\": ..., \"sweetness\": ..., "
            "\"liked_flavors\": [...], \"disliked_flavors\": [...]}\n"
            "- '산미가 너무 셌다' → acidity: \"lower\" (다음엔 산미가 더 낮은 게 좋다는 뜻)\n"
            "- 향미는 다음 중에서만: fruity, floral, sweet, nutty/cocoa, roasted, spices, sour/fermented\n"
            "- 후기에 근거가 없으면 null/빈 배열")},
    ]
```

`app/core/explain.py`:
```python
import json

from app.core.flavors import CATEGORY_KO
from app.models import ATTRS, Item, Prediction, Profile

ATTR_KO = {"acidity": "산미", "body": "바디", "sweetness": "단맛"}
CONFIDENCE_KO = {"high": "높음", "medium": "보통", "low": "낮음"}
CLOSE = 0.75
SYSTEM_PROMPT = ("너는 카페에서 손님에게 커피를 추천하는 친절한 바리스타다. 주어진 데이터만 근거로, 왜 이 음료가 손님 취향에 "
                 "맞는지(또는 안 맞는지) 한국어 2문장 이내로 설명해라. 데이터에 없는 수치나 사실을 지어내지 마라. /no_think")


def template_explanation(item: Item, profile: Profile, score: float, tag_ko: dict[str, str],
                         violation: str | None = None) -> str:
    parts = [f"취향 적합도 {round(score * 100)}%."]
    close = [ATTR_KO[a] for a in ATTRS if item.attr(a) is not None and abs(item.attr(a) - getattr(profile, a)) <= CLOSE]
    if close:
        parts.append(f"{', '.join(close)}이(가) 선호와 가까워요.")
    if item.tags:
        parts.append("향미: " + ", ".join(tag_ko.get(t, t) for t in item.tags[:3]) + ".")
    if item.order_decaf:
        surcharge = f" (+{item.decaf_surcharge_krw}원)" if item.decaf_surcharge_krw else ""
        parts.append(f"디카페인으로 변경해서 주문하세요{surcharge}.")
    if item.source == "predicted":
        parts.append(f"유사 원두 기반 예측이에요(신뢰도 {CONFIDENCE_KO[item.confidence]}).")
    if violation:
        parts.insert(0, f"주의: {violation}.")
    return " ".join(parts)


def explain_messages(item: Item, profile: Profile, score: float, prediction: Prediction | None = None) -> list[dict]:
    payload = {
        "음료": item.name, "브랜드": item.brand, "산미": item.acidity, "바디": item.body, "단맛": item.sweetness,
        "향미": list(item.tags), "디카페인": item.is_decaf or item.order_decaf, "우유": item.is_milk,
        "적합도": round(score * 100),
        "손님 선호": {"산미": round(profile.acidity, 1), "바디": round(profile.body, 1),
                  "단맛": round(profile.sweetness, 1),
                  "좋아하는 향미": [CATEGORY_KO.get(c, c) for c, w in profile.flavor_weights.items() if w > 0.2]},
        "근거": prediction.evidence if prediction else None,
    }
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def card(item: Item, score: float, template: str, violation: str | None = None,
         prediction: Prediction | None = None) -> dict:
    c = {"key": item.key, "name": item.name, "brand": item.brand, "score": round(score * 100),
         "source": item.source, "confidence": item.confidence, "acidity": item.acidity, "body": item.body,
         "sweetness": item.sweetness, "tags": list(item.tags), "is_decaf": item.is_decaf,
         "order_decaf": item.order_decaf, "decaf_surcharge_krw": item.decaf_surcharge_krw,
         "caffeine_mg": item.caffeine_mg, "is_milk": item.is_milk, "coffee_id": item.coffee_id,
         "menu_item_id": item.menu_item_id, "violation": violation, "template": template}
    if prediction is not None:
        c["evidence"] = prediction.evidence
        c["n_neighbors"] = prediction.n_neighbors
    return c
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_parse_explain.py -v` → Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/parse.py app/core/explain.py tests/app/test_parse_explain.py
git commit -m "feat(core): 원두 텍스트 파싱(규칙 우선), 템플릿 설명, LLM 메시지와 결과 카드"
```

---

### Task 7: 비동기 스트리밍 LLM 클라이언트 + 설명 모델 선정

**Files:**
- Create: `app/llm.py`, `tests/app/test_llm_stream.py`
- Modify: `config/models.yaml`

**Interfaces:**
- Consumes: `pipeline.llm.LLMError`, `Target`, `extract_json`, `load_targets`
- Produces: `app.llm.astream_text(task: str, messages: list[dict], transport=None) -> AsyncIterator[str]` (content 델타만, reasoning 델타 무시, 첫 토큰 전 실패 시 폴백 대상으로, 토큰이 나온 뒤 끊기면 `LLMError`), `app.llm.achat_json(task, messages, schema, transport=None) -> BaseModel`

- [ ] **Step 1: `config/models.yaml`의 `tasks:` 끝에 추가**

```yaml
  # 2단계 앱: 추천 설명(스트리밍), 후기·원두 파싱. 원격 실패 시 로컬 폴백.
  explain:
    provider: nvidia
    model: nvidia/nemotron-3-super-120b-a12b
    timeout: 20
    max_tokens: 1500
    fallback: {provider: ollama, model: "qwen3.5:9b", timeout: 60, max_tokens: 400, extra: {reasoning_effort: "none"}}
  parse_note:
    provider: nvidia
    model: nvidia/nemotron-3-super-120b-a12b
    timeout: 20
    max_tokens: 1500
    fallback: {provider: ollama, model: "qwen3.5:9b", timeout: 60, max_tokens: 400, extra: {reasoning_effort: "none"}}
  parse_bean:
    provider: nvidia
    model: nvidia/nemotron-3-super-120b-a12b
    timeout: 20
    max_tokens: 1500
    fallback: {provider: ollama, model: "qwen3.5:9b", timeout: 60, max_tokens: 400, extra: {reasoning_effort: "none"}}
```

- [ ] **Step 2: 실패하는 테스트** — `tests/app/test_llm_stream.py`

```python
import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

import app.llm as llm
from pipeline.llm import LLMError, Target


def sse(*chunks, done=True):
    lines = [f"data: {json.dumps({'choices': [{'delta': d}]})}" for d in chunks]
    if done:
        lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def targets(monkeypatch, primary="p", fallback="f"):
    t = lambda m: Target("x", "http://llm.test/v1", None, m, 5.0)  # noqa: E731
    monkeypatch.setattr(llm, "load_targets", lambda task: (t(primary), t(fallback) if fallback else None))


def collect(task, transport):
    async def run():
        return [tok async for tok in llm.astream_text(task, [{"role": "user", "content": "x"}], transport=transport)]
    return asyncio.run(run())


def test_stream_yields_content_and_skips_reasoning(monkeypatch):
    targets(monkeypatch)
    body = sse({"reasoning_content": "hmm"}, {"content": "산미가 "}, {"content": "좋아요"})
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    assert collect("explain", tr) == ["산미가 ", "좋아요"]


def test_stream_falls_back_before_first_token(monkeypatch):
    targets(monkeypatch)

    def handler(r):
        model = json.loads(r.content)["model"]
        if model == "p":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=sse({"content": "폴백"}))
    assert collect("explain", httpx.MockTransport(handler)) == ["폴백"]


def test_empty_stream_falls_back_and_total_failure_raises(monkeypatch):
    targets(monkeypatch)
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=sse({"reasoning_content": "only thinking"})))
    with pytest.raises(LLMError):
        collect("explain", tr)


def test_stream_broken_after_output_raises(monkeypatch):
    targets(monkeypatch, fallback=None)

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse({"content": "부분"}, done=False).encode()
            raise httpx.ReadError("connection lost")

    tr = httpx.MockTransport(lambda r: httpx.Response(200, stream=Broken()))
    with pytest.raises(LLMError):
        collect("explain", tr)


class Out(BaseModel):
    acidity: str | None = None


def test_achat_json_retries_then_falls_back(monkeypatch):
    targets(monkeypatch)
    calls = []

    def handler(r):
        model = json.loads(r.content)["model"]
        calls.append(model)
        if model == "p":
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"acidity": "lower"}'}}]})
    out = asyncio.run(llm.achat_json("parse_note", [{"role": "user", "content": "x"}], Out,
                                     transport=httpx.MockTransport(handler)))
    assert out == Out(acidity="lower")
    assert calls == ["p", "p", "f"]
```

- [ ] **Step 3: 실패 확인** — Run: `uv run pytest tests/app/test_llm_stream.py -v` → Expected: FAIL (`app.llm` 없음)

- [ ] **Step 4: 구현** — `app/llm.py`

```python
"""Async LLM calls for the app: token streaming for explanations, JSON for parsing.

Reuses the provider/task config of pipeline.llm (config/models.yaml) and its fallback rule:
if the primary target fails before producing any content, the fallback target is tried.
"""
import json
from typing import AsyncIterator

import httpx
from pydantic import BaseModel, ValidationError

from pipeline.llm import LLMError, Target, extract_json, load_targets


def _headers(t: Target) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if t.api_key:
        h["Authorization"] = f"Bearer {t.api_key}"
    return h


def _payload(t: Target, messages: list[dict], **extra) -> dict:
    p = {"model": t.model, "messages": messages, "max_tokens": t.max_tokens, **extra}
    if t.extra:
        p.update(t.extra)
    return p


async def _stream_once(t: Target, messages: list[dict], transport) -> AsyncIterator[str]:
    async with httpx.AsyncClient(base_url=t.base_url, timeout=t.timeout, transport=transport) as client:
        async with client.stream("POST", "/chat/completions", headers=_headers(t),
                                 json=_payload(t, messages, temperature=0.3, stream=True)) as r:
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code} from {t.model}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = (json.loads(data)["choices"][0].get("delta") or {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    continue
                if delta:
                    yield delta


async def astream_text(task: str, messages: list[dict], transport=None) -> AsyncIterator[str]:
    primary, fallback = load_targets(task)
    last: Exception | None = None
    for t in [primary] + ([fallback] if fallback else []):
        started = False
        try:
            async for tok in _stream_once(t, messages, transport):
                started = True
                yield tok
        except (httpx.HTTPError, LLMError) as e:
            if started:
                raise LLMError(f"stream from {t.model} broke after output: {e}") from e
            last = e
            continue
        if started:
            return
        last = LLMError(f"empty stream from {t.model}")
    raise LLMError(f"all targets failed for {task}: {last}")


async def _chat_once(t: Target, messages: list[dict], transport) -> str:
    async with httpx.AsyncClient(base_url=t.base_url, timeout=t.timeout, transport=transport) as client:
        try:
            r = await client.post("/chat/completions", headers=_headers(t), json=_payload(t, messages, temperature=0))
        except httpx.HTTPError as e:
            raise LLMError(f"{type(e).__name__} from {t.model}: {e}") from e
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code} from {t.model}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"malformed response from {t.model}") from e
    if not content or not content.strip():
        raise LLMError(f"empty content from {t.model}")
    return content


async def achat_json(task: str, messages: list[dict], schema: type[BaseModel], transport=None) -> BaseModel:
    primary, fallback = load_targets(task)
    last: Exception | None = None
    for t in [primary] + ([fallback] if fallback else []):
        for _ in range(2):                      # one retry on invalid JSON, same target
            try:
                return schema.model_validate(extract_json(await _chat_once(t, messages, transport)))
            except LLMError as e:               # transport failure: move to the next target
                last = e
                break
            except (ValueError, ValidationError) as e:
                last = e
    raise LLMError(f"achat_json failed for {task}: {last}")
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/app/test_llm_stream.py -v && uv run pytest -q` → Expected: 전부 PASS

- [ ] **Step 6: 설명 모델 선정 (네트워크, 테스트 아님)** — 한국어 설명의 첫 글자 표시 시간(TTFT)과 총 시간을 후보별로 잰다. 스크래치 파일(레포 밖)에 아래 스크립트를 두고 `uv run python <path>`로 실행:

```python
import asyncio, time
import app.llm as llm
from pipeline.llm import Target, load_targets
MSG = [{"role": "system", "content": "너는 친절한 바리스타다. 한국어 2문장 이내로 답해라. /no_think"},
       {"role": "user", "content": '{"음료": "에티오피아 예가체프 워시드", "산미": 4, "바디": 2, "향미": ["lemon","jasmine"], "손님 선호": {"산미": 4.5}}'}]
CANDIDATES = ["nvidia/nemotron-3-super-120b-a12b", "google/gemma-3-12b-it", "meta/llama-3.2-11b-vision-instruct",
              "deepseek-ai/deepseek-v4.1-flash"]
base, _ = load_targets("explain")
async def one(model):
    t = Target(base.provider, base.base_url, base.api_key, model, 30.0, 1500)
    llm.load_targets = lambda task: (t, None)
    start = time.time(); first = None; out = []
    try:
        async for tok in llm.astream_text("explain", MSG):
            first = first or time.time() - start
            out.append(tok)
        return model, round(first, 1), round(time.time() - start, 1), "".join(out)[:80]
    except Exception as e:
        return model, None, round(time.time() - start, 1), f"FAIL {e}"[:80]
async def main():
    for m in CANDIDATES:
        print(await one(m))
asyncio.run(main())
```
선정 규칙: 한국어로 자연스러운 답을 낸 후보 중 **TTFT가 가장 짧은 모델**(동률이면 총 시간). 결과 표(모델·TTFT·총 시간·출력 앞부분)를 보고서에 기록하고, 선정 모델로 `config/models.yaml`의 `explain`·`parse_note`·`parse_bean` `model` 값을 바꾼다(모두 실패하면 nemotron 유지하고 보고서에 명시). 이 표는 Task 14의 ADR 실측 근거로 쓴다.

- [ ] **Step 7: Commit**

```bash
git add app/llm.py tests/app/test_llm_stream.py config/models.yaml
git commit -m "feat(llm): 비동기 스트리밍·JSON LLM 클라이언트(폴백), 설명 모델 실측 선정"
```

---

### Task 8: Repo — DB 접근 계층

**Files:**
- Create: `app/repo.py`, `tests/app/test_repo.py`

**Interfaces:**
- Consumes: `pipeline.query.to_vector_literal`, `pipeline.rules.normalize_country`, `app.models`, `app.core.flavors.build_tag_to_category`, `app.core.scoring.is_milk_drink`, `LOW_CAFFEINE_MG`
- Produces: `class Repo(url: str, min_size=1, max_size=5)` 메서드:
  - `close()`; `create_user() -> str`; `user_exists(uid) -> bool`; `set_nickname(uid, nickname)`; `get_nickname(uid) -> str | None`
  - `get_profile(uid) -> Profile | None`; `save_profile(uid, profile, tasting_id=None)` (upsert + history 스냅샷)
  - `history(uid, limit=20) -> list[dict]`; `recent_tastings(uid, limit=20) -> list[dict]`
  - `taxonomy() -> tuple[dict[str,str], dict[str,str]]` (tag→category, tag→한국어) — 캐시
  - `list_brands() -> list[dict]`; `brand_items(brand_key, want_decaf: bool) -> list[Item]`; `get_menu_item(menu_item_id, want_decaf) -> Item | None`
  - `get_coffee(coffee_id) -> Item | None`; `match_coffee(text) -> Item | None`; `search_coffees(q, limit=8) -> list[dict]`
  - `neighbors(vec, k=10, origin=None, process=None, exclude_id=None) -> list[Neighbor]`; `fallback_neighbors(origin, process, limit=50) -> list[Neighbor]`; `coffee_embedding(coffee_id) -> list[float] | None`
  - `sample_coffees() -> list[dict]` (온보딩 샘플 3개: `coffee_id,name,summary,tags`)
  - `save_tasting(uid, *, coffee_id=None, menu_item_id=None, input_text=None, predicted=None, rating, note=None, parsed_signals=None) -> int`
  - `raw_menu(menu_item_id) -> dict | None`, `random_coffees_with_attrs(n, seed) -> list[Item]`, `random_coffee_ids_for_loo(n, seed) -> list[int]` (평가용)

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_repo.py`

```python
import pytest
from psycopg.types.json import Jsonb

from app.models import Profile
from app.repo import Repo
from pipeline.query import to_vector_literal

pytestmark = pytest.mark.db


def vec(i):
    v = [0.0] * 1024
    v[i] = 1.0
    return v


@pytest.fixture
def repo(db_conn):
    from tests.conftest import TEST_URL
    c = db_conn
    c.execute("INSERT INTO flavor_taxonomy (key, level, name_en, name_ko) VALUES "
              "('sca:fruity', 1, 'fruity', '과일'), ('sca:fruity>citrus fruit', 2, 'citrus fruit', '시트러스')")
    c.execute("UPDATE flavor_taxonomy SET parent_id = (SELECT id FROM flavor_taxonomy WHERE key='sca:fruity') "
              "WHERE key = 'sca:fruity>citrus fruit'")
    for i, (name, origin, process, decaf, acid, tags) in enumerate([
            ("Ethiopia Yirgacheffe Washed", "Ethiopia", "washed", False, 5, ["citrus fruit"]),
            ("Decaf Ethiopia Sidamo", "Ethiopia", "natural", True, 4, ["citrus fruit"]),
            ("Brazil Cerrado", "Brazil", "natural", False, 1, ["chocolate"])]):
        c.execute("INSERT INTO coffees (key, name, roaster, origin_country, process, is_decaf, acidity, body, "
                  "sweetness, flavor_tags, flavor_summary, embedding, source, collected_at) "
                  "VALUES (%s,%s,'R',%s,%s,%s,%s,3,3,%s,%s,%s::vector,'t','2026-09-26')",
                  (f"c{i}", name, origin, process, decaf, acid, tags, f"summary {i}", to_vector_literal(vec(i))))
    bean = Jsonb({"acidity": 2, "body": 4, "sweetness": 2, "flavor_tags": ["chocolate"]})
    dbean = Jsonb({"acidity": 2, "body": 3, "sweetness": 3, "flavor_tags": ["caramelized"]})
    c.execute("INSERT INTO brands (key, name, decaf_available, decaf_surcharge_krw, verified_at, bean, decaf_bean) VALUES "
              "('brand:sb', '스타벅스', true, 300, '2026-09-24', %s, %s), "
              "('brand:tw', '투썸', true, 200, '2026-09-24', %s, %s)", (bean, dbean, bean, dbean))
    c.execute("INSERT INTO menu_items (key, brand_id, name, is_decaf, decaf_option, caffeine_mg, collected_at) VALUES "
              "('m1', (SELECT id FROM brands WHERE key='brand:sb'), '아메리카노', false, true, 150, '2026-09-24'), "
              "('m2', (SELECT id FROM brands WHERE key='brand:sb'), '카페 라떼', false, true, 75, '2026-09-24')")
    c.commit()
    r = Repo(TEST_URL)
    yield r
    r.close()


def test_users_profiles_history(repo):
    uid = repo.create_user()
    assert repo.user_exists(uid) and not repo.user_exists("not-a-uuid")
    assert not repo.user_exists("00000000-0000-0000-0000-000000000000")
    assert repo.get_profile(uid) is None
    repo.save_profile(uid, Profile(caffeine_rule="decaf_only", acidity=4.5))
    repo.save_profile(uid, Profile(caffeine_rule="decaf_only", acidity=4.0, n_updates=1))
    assert repo.get_profile(uid).acidity == 4.0
    assert [h["snapshot"]["acidity"] for h in repo.history(uid)] == [4.0, 4.5]
    repo.set_nickname(uid, "가연")
    assert repo.get_nickname(uid) == "가연"


def test_taxonomy_maps(repo):
    tag_to_cat, tag_ko = repo.taxonomy()
    assert tag_to_cat == {"citrus fruit": "fruity"}
    assert tag_ko["citrus fruit"] == "시트러스"


def test_brand_items_decaf_option_and_synthetic_menu(repo):
    items = {i.name: i for i in repo.brand_items("brand:sb", want_decaf=True)}
    am = items["아메리카노"]
    assert (am.order_decaf, am.decaf_surcharge_krw, am.brand, am.source) == (True, 300, "스타벅스", "brand_bean")
    assert am.tags == ("caramelized",)                       # decaf bean attributes used
    assert items["카페 라떼"].is_milk is True
    plain = {i.name: i for i in repo.brand_items("brand:sb", want_decaf=False)}
    assert plain["아메리카노"].order_decaf is False and plain["아메리카노"].tags == ("chocolate",)
    synthetic = repo.brand_items("brand:tw", want_decaf=True)   # brand without menu rows
    assert [i.name for i in synthetic] == ["아메리카노", "카페라떼"]
    assert all(i.decaf_option and i.menu_item_id is None for i in synthetic)
    assert repo.brand_items("brand:none", want_decaf=False) == []
    assert repo.get_menu_item(am.menu_item_id, want_decaf=True).order_decaf is True


def test_coffee_lookup_search_and_match(repo):
    hit = repo.search_coffees("ethiopia")
    assert [h["name"] for h in hit] == ["Decaf Ethiopia Sidamo", "Ethiopia Yirgacheffe Washed"]   # shorter name first
    assert [h["name"] for h in repo.search_coffees("브라질")] == ["Brazil Cerrado"]      # Korean origin
    assert repo.search_coffees("%") == [] and repo.search_coffees("a") == []           # wildcard/too short
    it = repo.match_coffee("brazil cerrado")
    assert it.source == "db" and it.coffee_id is not None and it.acidity == 1
    assert repo.match_coffee("unknown bean") is None
    assert repo.get_coffee(it.coffee_id).name == "Brazil Cerrado"


def test_neighbors_prefer_same_origin_and_exclude(repo):
    target = repo.match_coffee("Ethiopia Yirgacheffe Washed").coffee_id
    near = repo.neighbors(vec(0), k=2, exclude_id=target)
    assert target not in [n.coffee_id for n in near] and len(near) == 2
    assert repo.coffee_embedding(target)[0] == 1.0
    fb = repo.fallback_neighbors("Ethiopia", None)
    assert {n.name for n in fb} == {"Ethiopia Yirgacheffe Washed", "Decaf Ethiopia Sidamo"}
    assert repo.fallback_neighbors(None, None) == []


def test_save_tasting_and_recent(repo):
    uid = repo.create_user()
    cid = repo.match_coffee("Brazil Cerrado").coffee_id
    tid = repo.save_tasting(uid, coffee_id=cid, rating=4, note="고소해요", parsed_signals={"body": "higher"})
    repo.save_tasting(uid, input_text="동네 블렌드", predicted={"acidity": 3}, rating=2)
    rows = repo.recent_tastings(uid)
    assert [r["name"] for r in rows] == ["동네 블렌드", "Brazil Cerrado"] and rows[1]["id"] == tid
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_repo.py -v` → Expected: FAIL (`app.repo` 없음)

- [ ] **Step 3: 구현** — `app/repo.py`

```python
"""All SQL for the app. Synchronous psycopg pool; async graph nodes call these via asyncio.to_thread."""
import json
import random
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.flavors import build_tag_to_category
from app.core.scoring import LOW_CAFFEINE_MG, is_milk_drink
from app.models import Item, Neighbor, Profile
from pipeline.query import to_vector_literal
from pipeline.rules import normalize_country

MIN_FILTERED_NEIGHBORS = 5
SYNTHETIC_MENU = ("아메리카노", "카페라떼")      # for brands without scraped menus
COFFEE_COLS = "id, name, roaster, origin_country, process, is_decaf, acidity, body, sweetness, flavor_tags"


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _coffee_item(r: dict) -> Item:
    return Item(key=f"coffee:{r['id']}", name=r["name"], source="db", acidity=r["acidity"], body=r["body"],
                sweetness=r["sweetness"], tags=tuple(r["flavor_tags"] or ()), is_decaf=r["is_decaf"],
                confidence="high", brand=r["roaster"], coffee_id=r["id"], origin_country=r["origin_country"],
                process=r["process"])


class Repo:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 5):
        self.pool = ConnectionPool(url, min_size=min_size, max_size=max_size,
                                   kwargs={"row_factory": dict_row}, open=True)
        self._taxonomy: tuple[dict, dict] | None = None

    def close(self) -> None:
        self.pool.close()

    def _all(self, sql: str, params=None) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params=None) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    # ---- users & profiles -------------------------------------------------
    def create_user(self) -> str:
        return str(self._one("INSERT INTO users DEFAULT VALUES RETURNING id")["id"])

    def user_exists(self, uid: str | None) -> bool:
        try:
            UUID(str(uid))
        except ValueError:
            return False
        return self._one("SELECT 1 AS ok FROM users WHERE id = %s", (uid,)) is not None

    def set_nickname(self, uid: str, nickname: str | None) -> None:
        self._one("UPDATE users SET nickname = %s WHERE id = %s RETURNING id", (nickname, uid))

    def get_nickname(self, uid: str) -> str | None:
        row = self._one("SELECT nickname FROM users WHERE id = %s", (uid,))
        return row["nickname"] if row else None

    def get_profile(self, uid: str) -> Profile | None:
        r = self._one("SELECT * FROM taste_profiles WHERE user_id = %s", (uid,))
        if r is None:
            return None
        return Profile(caffeine_rule=r["caffeine_rule"], milk_ok=r["milk_ok"], acidity=r["acidity"], body=r["body"],
                       sweetness=r["sweetness"], flavor_weights=r["flavor_weights"], n_updates=r["n_updates"])

    def save_profile(self, uid: str, p: Profile, tasting_id: int | None = None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO taste_profiles (user_id, caffeine_rule, milk_ok, acidity, body, sweetness, flavor_weights,"
                " n_updates, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) ON CONFLICT (user_id) DO UPDATE SET"
                " caffeine_rule = EXCLUDED.caffeine_rule, milk_ok = EXCLUDED.milk_ok, acidity = EXCLUDED.acidity,"
                " body = EXCLUDED.body, sweetness = EXCLUDED.sweetness, flavor_weights = EXCLUDED.flavor_weights,"
                " n_updates = EXCLUDED.n_updates, updated_at = now()",
                (uid, p.caffeine_rule, p.milk_ok, p.acidity, p.body, p.sweetness, Jsonb(p.flavor_weights), p.n_updates))
            conn.execute("INSERT INTO profile_history (user_id, snapshot, tasting_id) VALUES (%s, %s, %s)",
                         (uid, Jsonb(p.to_dict()), tasting_id))

    def history(self, uid: str, limit: int = 20) -> list[dict]:
        return self._all("SELECT snapshot, tasting_id, created_at FROM profile_history WHERE user_id = %s"
                         " ORDER BY id DESC LIMIT %s", (uid, limit))

    def recent_tastings(self, uid: str, limit: int = 20) -> list[dict]:
        return self._all(
            "SELECT t.id, t.rating, t.note, t.created_at, coalesce(c.name, m.name, t.input_text) AS name"
            " FROM tastings t LEFT JOIN coffees c ON c.id = t.coffee_id LEFT JOIN menu_items m ON m.id = t.menu_item_id"
            " WHERE t.user_id = %s ORDER BY t.id DESC LIMIT %s", (uid, limit))

    def save_tasting(self, uid: str, *, coffee_id: int | None = None, menu_item_id: int | None = None,
                     input_text: str | None = None, predicted: dict | None = None, rating: int,
                     note: str | None = None, parsed_signals: dict | None = None) -> int:
        return self._one(
            "INSERT INTO tastings (user_id, coffee_id, menu_item_id, input_text, predicted, rating, note, parsed_signals)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (uid, coffee_id, menu_item_id, input_text, Jsonb(predicted) if predicted else None, rating, note,
             Jsonb(parsed_signals) if parsed_signals else None))["id"]

    # ---- taxonomy ---------------------------------------------------------
    def taxonomy(self) -> tuple[dict[str, str], dict[str, str]]:
        if self._taxonomy is None:
            rows = self._all("SELECT key, level, name_en, name_ko FROM flavor_taxonomy")
            tag_to_cat = build_tag_to_category((r["key"], r["level"], r["name_en"]) for r in rows)
            tag_ko = {r["name_en"].lower(): r["name_ko"] for r in rows if r["name_ko"]}
            self._taxonomy = (tag_to_cat, tag_ko)
        return self._taxonomy

    # ---- brands & menus ---------------------------------------------------
    def list_brands(self) -> list[dict]:
        return self._all("SELECT b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, b.notes,"
                         " EXISTS (SELECT 1 FROM menu_items m WHERE m.brand_id = b.id) AS has_menu"
                         " FROM brands b ORDER BY b.name")

    def brand_items(self, brand_key: str, want_decaf: bool) -> list[Item]:
        b = self._one("SELECT * FROM brands WHERE key = %s", (brand_key,))
        if b is None:
            return []
        menus = self._all("SELECT id, name, is_decaf, decaf_option, caffeine_mg FROM menu_items"
                          " WHERE brand_id = %s ORDER BY id", (b["id"],))
        if not menus:
            menus = [{"id": None, "name": n, "is_decaf": False, "decaf_option": b["decaf_available"],
                      "caffeine_mg": None} for n in SYNTHETIC_MENU]
        return [self._menu_item(b, m, want_decaf) for m in menus]

    def _menu_item(self, b: dict, m: dict, want_decaf: bool) -> Item:
        low_enough = m["caffeine_mg"] is not None and m["caffeine_mg"] <= LOW_CAFFEINE_MG
        order_decaf = bool(want_decaf and m["decaf_option"] and not m["is_decaf"] and not low_enough)
        bean = (b["decaf_bean"] if (m["is_decaf"] or order_decaf) and b["decaf_bean"] else b["bean"]) or {}
        key = f"menu:{m['id']}" if m["id"] is not None else f"{b['key']}:{m['name']}"
        return Item(key=key, name=m["name"], source="brand_bean", acidity=bean.get("acidity"), body=bean.get("body"),
                    sweetness=bean.get("sweetness"), tags=tuple(bean.get("flavor_tags", ())), is_decaf=m["is_decaf"],
                    decaf_option=m["decaf_option"], order_decaf=order_decaf, caffeine_mg=m["caffeine_mg"],
                    is_milk=is_milk_drink(m["name"]), confidence="medium", brand=b["name"],
                    decaf_surcharge_krw=b["decaf_surcharge_krw"], menu_item_id=m["id"])

    def get_menu_item(self, menu_item_id: int, want_decaf: bool) -> Item | None:
        row = self._one("SELECT b.key FROM menu_items m JOIN brands b ON b.id = m.brand_id WHERE m.id = %s",
                        (menu_item_id,))
        if row is None:
            return None
        return next((i for i in self.brand_items(row["key"], want_decaf) if i.menu_item_id == menu_item_id), None)

    def raw_menu(self, menu_item_id: int) -> dict | None:
        return self._one("SELECT m.name, m.is_decaf, m.decaf_option, m.caffeine_mg, b.decaf_available"
                         " FROM menu_items m JOIN brands b ON b.id = m.brand_id WHERE m.id = %s", (menu_item_id,))

    # ---- coffees ----------------------------------------------------------
    def get_coffee(self, coffee_id: int) -> Item | None:
        r = self._one(f"SELECT {COFFEE_COLS} FROM coffees WHERE id = %s", (coffee_id,))
        return _coffee_item(r) if r else None

    def match_coffee(self, text: str) -> Item | None:
        t = " ".join((text or "").split()).lower()
        if not t:
            return None
        r = self._one(f"SELECT {COFFEE_COLS} FROM coffees WHERE lower(name) = %s"
                      " OR lower(coalesce(roaster, '') || ' ' || name) = %s ORDER BY id LIMIT 1", (t, t))
        return _coffee_item(r) if r else None

    def search_coffees(self, q: str, limit: int = 8) -> list[dict]:
        q = (q or "").strip()
        if len(q) < 2 or not q.strip("%_\\"):
            return []
        like = f"%{_escape_like(q)}%"
        country = normalize_country(q)
        return self._all(
            "SELECT id, name, roaster, origin_country, is_decaf FROM coffees"
            " WHERE name ILIKE %(like)s OR roaster ILIKE %(like)s OR origin_country = %(country)s"
            " ORDER BY (name ILIKE %(like)s) DESC, length(name), id LIMIT %(limit)s",
            {"like": like, "country": country, "limit": limit})

    def coffee_embedding(self, coffee_id: int) -> list[float] | None:
        r = self._one("SELECT embedding::text AS e FROM coffees WHERE id = %s", (coffee_id,))
        return json.loads(r["e"]) if r and r["e"] else None

    def neighbors(self, vec: list[float], k: int = 10, origin: str | None = None, process: str | None = None,
                  exclude_id: int | None = None) -> list[Neighbor]:
        v = to_vector_literal(vec)
        base = " AND id <> %(ex)s" if exclude_id is not None else ""

        def run(extra: str) -> list[Neighbor]:
            with self.pool.connection() as conn, conn.transaction():
                conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                conn.execute("SET LOCAL hnsw.ef_search = 200")
                rows = conn.execute(
                    "SELECT id, name, acidity, body, sweetness, flavor_tags, 1 - (embedding <=> %(v)s::vector) AS sim"
                    f" FROM coffees WHERE embedding IS NOT NULL{base}{extra}"
                    " ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s",
                    {"v": v, "k": k, "ex": exclude_id, "o": origin, "p": process}).fetchall()
            return [Neighbor(r["id"], r["name"], float(r["sim"]), r["acidity"], r["body"], r["sweetness"],
                             tuple(r["flavor_tags"] or ())) for r in sorted(rows, key=lambda r: -r["sim"])]

        if origin or process:
            extra = (" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else "")
            hits = run(extra)
            if len(hits) >= MIN_FILTERED_NEIGHBORS:
                return hits
        return run("")

    def fallback_neighbors(self, origin: str | None, process: str | None, limit: int = 50) -> list[Neighbor]:
        if not origin and not process:
            return []
        rows = self._all(
            "SELECT id, name, acidity, body, sweetness, flavor_tags FROM coffees WHERE true"
            + (" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else "")
            + " ORDER BY id LIMIT %(limit)s", {"o": origin, "p": process, "limit": limit})
        return [Neighbor(r["id"], r["name"], 1.0, r["acidity"], r["body"], r["sweetness"],
                         tuple(r["flavor_tags"] or ())) for r in rows]

    def sample_coffees(self) -> list[dict]:
        queries = [
            "origin_country = 'Ethiopia' AND process = 'washed' AND acidity >= 4",      # bright washed
            "origin_country = 'Brazil' AND acidity <= 2",                               # chocolatey
            "process IN ('natural', 'anaerobic') AND flavor_tags && ARRAY['winey','fermented','alcohol fermented']",
        ]
        out = []
        for where in queries:
            r = self._one("SELECT id, name, flavor_summary, flavor_tags FROM coffees WHERE " + where +
                          " AND cardinality(flavor_tags) > 0 ORDER BY id LIMIT 1")
            if r:
                out.append({"coffee_id": r["id"], "name": r["name"], "summary": r["flavor_summary"],
                            "tags": r["flavor_tags"]})
        return out

    # ---- evaluation helpers -----------------------------------------------
    def random_coffees_with_attrs(self, n: int, seed: int) -> list[Item]:
        rows = self._all(f"SELECT {COFFEE_COLS} FROM coffees WHERE acidity IS NOT NULL AND body IS NOT NULL"
                         " AND sweetness IS NOT NULL ORDER BY id")
        return [_coffee_item(r) for r in random.Random(seed).sample(rows, min(n, len(rows)))]

    def random_coffee_ids_for_loo(self, n: int, seed: int) -> list[int]:
        rows = self._all("SELECT id FROM coffees WHERE embedding IS NOT NULL AND acidity IS NOT NULL"
                         " AND body IS NOT NULL ORDER BY id")
        ids = [r["id"] for r in rows]
        return random.Random(seed).sample(ids, min(n, len(ids)))
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_repo.py -v && uv run pytest -q` → Expected: 전부 PASS (db 테스트 skip 없음)

- [ ] **Step 5: Commit**

```bash
git add app/repo.py tests/app/test_repo.py
git commit -m "feat(app): Repo — 사용자·프로필·기록, 브랜드 메뉴(디카페인 변경·기본 메뉴), 원두 검색·이웃 조회"
```

---

### Task 9: Deps + 설명 스트리밍 공통 + `recommend` 그래프

**Files:**
- Modify: `app/graphs/__init__.py`
- Create: `app/graphs/common.py`, `app/graphs/recommend.py`, `tests/app/fakes.py`, `tests/app/test_graph_recommend.py`

**Interfaces:**
- Consumes: Repo 메서드(`taxonomy`, `brand_items`), core(`passes`, `score_item`, `mmr_top_k`, `template_explanation`, `explain_messages`, `card`), `app.config.EXPLAIN_TASK`, `app.tracing.traced`
- Produces:
  - `app.graphs.Deps(repo, embed, stream_text, chat_json)` dataclass, `default_deps(repo) -> Deps`
  - `app.graphs.common.explain_to_stream(deps, item, profile, score, tag_ko, prediction=None, violation=None) -> dict` (`{"key","text","fallback"}`), 스트림 이벤트 `explain_delta{key,delta}` / `explain_done{key,text}` / `explain_fallback{key,text}`
  - `app.graphs.recommend.build_recommend_graph(deps) -> CompiledGraph`, 입력 `{"brand_key": str, "profile": Profile}`, 이벤트 `cards{cards:[...]}` 또는 `empty{reason}` 후 설명 이벤트
  - `app.graphs.recommend.empty_reason(profile, candidates) -> str`
  - `tests/app/fakes.py`: `FakeRepo`, `fake_deps(repo, tokens=..., fail_keys=(), parse=None, embed_fails=False) -> Deps`, `run_events(graph, inputs) -> list[dict]`

- [ ] **Step 1: 가짜 구현 작성** — `tests/app/fakes.py`

```python
import asyncio
import itertools
import uuid

from app.graphs import Deps
from app.models import Item, Neighbor, Profile
from pipeline.llm import LLMError

TAG_TO_CAT = {"lemon": "fruity", "jasmine": "floral", "chocolate": "nutty/cocoa", "caramelized": "sweet"}
TAG_KO = {"lemon": "레몬", "chocolate": "초콜릿"}


class FakeRepo:
    def __init__(self):
        self.users: dict[str, str | None] = {}
        self.profiles: dict[str, Profile] = {}
        self.history_rows: dict[str, list[dict]] = {}
        self.tastings: list[dict] = []
        self.coffees = {
            1: Item(key="coffee:1", name="Ethiopia Yirgacheffe Washed", source="db", acidity=5, body=2, sweetness=3,
                    tags=("lemon", "jasmine"), coffee_id=1, origin_country="Ethiopia", process="washed"),
            2: Item(key="coffee:2", name="Brazil Cerrado", source="db", acidity=1, body=4, sweetness=3,
                    tags=("chocolate",), coffee_id=2, origin_country="Brazil", process="natural"),
        }
        self.menu = {
            "brand:sb": [
                Item(key="menu:10", name="아메리카노", source="brand_bean", acidity=2, body=4, sweetness=2,
                     tags=("chocolate",), decaf_option=True, caffeine_mg=150, brand="스타벅스",
                     decaf_surcharge_krw=300, menu_item_id=10, confidence="medium"),
                Item(key="menu:11", name="카페 라떼", source="brand_bean", acidity=2, body=4, sweetness=3,
                     tags=("chocolate",), decaf_option=True, caffeine_mg=75, is_milk=True, brand="스타벅스",
                     menu_item_id=11, confidence="medium"),
                Item(key="menu:12", name="블론드 아메리카노", source="brand_bean", acidity=4, body=2, sweetness=3,
                     tags=("lemon",), decaf_option=False, caffeine_mg=170, brand="스타벅스", menu_item_id=12,
                     confidence="medium"),
                Item(key="menu:13", name="콜드 브루", source="brand_bean", acidity=3, body=3, sweetness=2,
                     tags=("caramelized",), decaf_option=False, caffeine_mg=155, brand="스타벅스", menu_item_id=13,
                     confidence="medium"),
            ],
            "brand:nodecaf": [Item(key="menu:20", name="에스프레소", source="brand_bean", acidity=3, body=4,
                                   sweetness=2, caffeine_mg=120, brand="X", menu_item_id=20, confidence="medium")],
        }
        self.tasting_ids = itertools.count(1)

    # users/profiles
    def create_user(self):
        uid = str(uuid.uuid4())
        self.users[uid] = None
        return uid

    def user_exists(self, uid):
        return uid in self.users

    def set_nickname(self, uid, nickname):
        self.users[uid] = nickname

    def get_nickname(self, uid):
        return self.users.get(uid)

    def get_profile(self, uid):
        return self.profiles.get(uid)

    def save_profile(self, uid, p, tasting_id=None):
        self.profiles[uid] = p
        self.history_rows.setdefault(uid, []).insert(0, {"snapshot": p.to_dict(), "tasting_id": tasting_id})

    def history(self, uid, limit=20):
        return self.history_rows.get(uid, [])[:limit]

    def recent_tastings(self, uid, limit=20):
        return [t for t in reversed(self.tastings) if t["user_id"] == uid][:limit]

    def save_tasting(self, uid, **kw):
        tid = next(self.tasting_ids)
        self.tastings.append({"id": tid, "user_id": uid, **kw})
        return tid

    # catalog
    def taxonomy(self):
        return TAG_TO_CAT, TAG_KO

    def list_brands(self):
        return [{"key": "brand:sb", "name": "스타벅스", "decaf_available": True, "decaf_surcharge_krw": 300,
                 "notes": "", "has_menu": True}]

    def brand_items(self, brand_key, want_decaf):
        items = self.menu.get(brand_key, [])
        if not want_decaf:
            return list(items)
        from dataclasses import replace
        return [replace(i, order_decaf=bool(i.decaf_option and not i.is_decaf
                                             and not (i.caffeine_mg is not None and i.caffeine_mg <= 100)))
                for i in items]

    def get_menu_item(self, menu_item_id, want_decaf):
        for brand_key in self.menu:
            for i in self.brand_items(brand_key, want_decaf):
                if i.menu_item_id == menu_item_id:
                    return i
        return None

    def get_coffee(self, coffee_id):
        return self.coffees.get(coffee_id)

    def match_coffee(self, text):
        t = " ".join((text or "").split()).lower()
        return next((c for c in self.coffees.values() if c.name.lower() == t), None)

    def search_coffees(self, q, limit=8):
        q = (q or "").strip().lower()
        if len(q) < 2:
            return []
        return [{"id": c.coffee_id, "name": c.name, "roaster": "R", "origin_country": c.origin_country,
                 "is_decaf": c.is_decaf} for c in self.coffees.values() if q in c.name.lower()][:limit]

    def neighbors(self, vec, k=10, origin=None, process=None, exclude_id=None):
        return [Neighbor(100 + i, f"n{i}", 0.9 - i * 0.05, 4 + (i % 2) * 0.5, 2, 3, ("lemon",)) for i in range(k)]

    def fallback_neighbors(self, origin, process, limit=50):
        if not origin and not process:
            return []
        return [Neighbor(200 + i, f"f{i}", 1.0, 3, 3, 3, ("chocolate",)) for i in range(4)]

    def sample_coffees(self):
        return [{"coffee_id": 1, "name": "Ethiopia Yirgacheffe Washed", "summary": "bright", "tags": ["lemon"]},
                {"coffee_id": 2, "name": "Brazil Cerrado", "summary": "choco", "tags": ["chocolate"]}]


def fake_deps(repo=None, tokens=("잘 ", "맞아요"), fail_keys=(), parse=None, embed_fails=False,
              json_fails=False) -> Deps:
    repo = repo or FakeRepo()
    calls = {"stream": 0, "json": 0, "embed": 0}

    async def stream_text(task, messages):
        calls["stream"] += 1
        content = messages[-1]["content"]
        if any(k in content for k in fail_keys):
            raise LLMError("boom")
        for t in tokens:
            await asyncio.sleep(0)
            yield t

    async def chat_json(task, messages, schema):
        calls["json"] += 1
        if json_fails:
            raise LLMError("json boom")
        return schema.model_validate(parse or {})

    async def embed(text):
        calls["embed"] += 1
        if embed_fails:
            raise LLMError("embed boom")
        return [0.0] * 8

    d = Deps(repo=repo, embed=embed, stream_text=stream_text, chat_json=chat_json)
    d.calls = calls
    return d


def run_events(graph, inputs) -> list[dict]:
    async def go():
        return [chunk async for chunk in graph.astream(inputs, stream_mode="custom")]
    return asyncio.run(go())
```

- [ ] **Step 2: 실패하는 테스트** — `tests/app/test_graph_recommend.py`

```python
from app.graphs.recommend import build_recommend_graph, empty_reason
from app.models import Profile
from tests.app.fakes import FakeRepo, fake_deps, run_events


def test_recommend_streams_cards_then_three_explanations():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps),
                        {"brand_key": "brand:sb", "profile": Profile(acidity=4, body=2, flavor_weights={"fruity": 0.8})})
    cards = next(e for e in events if e["type"] == "cards")["cards"]
    assert len(cards) == 3 and cards[0]["name"] == "블론드 아메리카노"
    assert all("template" in c and "text" not in c for c in cards)
    done = [e for e in events if e["type"] == "explain_done"]
    assert sorted(e["key"] for e in done) == sorted(c["key"] for c in cards)
    assert all(e["text"] == "잘 맞아요" for e in done)
    assert events.index(next(e for e in events if e["type"] == "cards")) < events.index(done[0])


def test_decaf_user_gets_only_decaf_capable_drinks_with_order_flag():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:sb",
                                                       "profile": Profile(caffeine_rule="decaf_only")})
    cards = next(e for e in events if e["type"] == "cards")["cards"]
    assert {c["name"] for c in cards} == {"아메리카노", "카페 라떼"}
    americano = next(c for c in cards if c["name"] == "아메리카노")
    assert americano["order_decaf"] is True and americano["decaf_surcharge_krw"] == 300
    assert "디카페인으로 변경해서 주문하세요 (+300원)." in americano["template"]


def test_one_failed_explanation_falls_back_others_stream():
    deps = fake_deps(fail_keys=("블론드",))
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:sb",
                                                       "profile": Profile(acidity=4, body=2)})
    fb = [e for e in events if e["type"] == "explain_fallback"]
    assert len(fb) == 1 and fb[0]["text"].startswith("취향 적합도")
    assert len([e for e in events if e["type"] == "explain_done"]) == 2


def test_empty_when_nothing_passes():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:nodecaf",
                                                       "profile": Profile(caffeine_rule="decaf_only")})
    assert events == [{"type": "empty", "reason": "이 브랜드는 디카페인 메뉴가 없어요"}]
    assert deps.calls["stream"] == 0


def test_empty_reasons():
    assert empty_reason(Profile(), []) == "이 브랜드의 메뉴 정보가 없어요"
    items = FakeRepo().menu["brand:sb"]
    assert empty_reason(Profile(milk_ok=False, caffeine_rule="decaf_only"), items[1:2]) == "조건에 맞는 메뉴가 없어요"
```

- [ ] **Step 3: 실패 확인** — Run: `uv run pytest tests/app/test_graph_recommend.py -v` → Expected: FAIL (import 오류)

- [ ] **Step 4: 구현**

`app/graphs/__init__.py`:
```python
import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable


@dataclass
class Deps:
    repo: Any
    embed: Callable[[str], Awaitable[list[float]]]
    stream_text: Callable[[str, list[dict]], AsyncIterator[str]]
    chat_json: Callable[[str, list[dict], type], Awaitable[Any]]


def default_deps(repo) -> Deps:
    from app import llm
    from pipeline.llm import embedder_for

    embedder = embedder_for("embed")

    async def embed(text: str) -> list[float]:
        return (await asyncio.to_thread(embedder.embed, [text]))[0]

    return Deps(repo=repo, embed=embed, stream_text=llm.astream_text, chat_json=llm.achat_json)
```

`app/graphs/common.py`:
```python
import httpx
from langgraph.config import get_stream_writer

from app.config import EXPLAIN_TASK
from app.core.explain import explain_messages, template_explanation
from app.models import Item, Prediction, Profile
from pipeline.llm import LLMError


async def explain_to_stream(deps, item: Item, profile: Profile, score: float, tag_ko: dict[str, str],
                            prediction: Prediction | None = None, violation: str | None = None) -> dict:
    """Stream an LLM explanation token by token; on any failure replace it with the template."""
    writer = get_stream_writer()
    template = template_explanation(item, profile, score, tag_ko, violation)
    parts: list[str] = []
    try:
        async for tok in deps.stream_text(EXPLAIN_TASK, explain_messages(item, profile, score, prediction)):
            parts.append(tok)
            writer({"type": "explain_delta", "key": item.key, "delta": tok})
        text = "".join(parts).strip()
        if not text:
            raise LLMError("empty explanation")
        writer({"type": "explain_done", "key": item.key, "text": text})
        return {"key": item.key, "text": text, "fallback": False}
    except (LLMError, httpx.HTTPError):
        writer({"type": "explain_fallback", "key": item.key, "text": template})
        return {"key": item.key, "text": template, "fallback": True}
```

`app/graphs/recommend.py`:
```python
import asyncio
import operator
from typing import Annotated, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.core.explain import card, template_explanation
from app.core.scoring import mmr_top_k, passes, score_item
from app.graphs.common import explain_to_stream
from app.models import Item, Profile
from app.tracing import traced

TOP_K = 3


class RecommendState(TypedDict, total=False):
    brand_key: str
    profile: Profile
    candidates: list[Item]
    ranked: list[dict]
    explanations: Annotated[list[dict], operator.add]


def empty_reason(profile: Profile, candidates: list[Item]) -> str:
    if not candidates:
        return "이 브랜드의 메뉴 정보가 없어요"
    if profile.caffeine_rule == "decaf_only" and not any(i.is_decaf or i.decaf_option for i in candidates):
        return "이 브랜드는 디카페인 메뉴가 없어요"
    return "조건에 맞는 메뉴가 없어요"


def build_recommend_graph(deps):
    async def load(state: RecommendState) -> dict:
        want_decaf = state["profile"].caffeine_rule in ("decaf_only", "low")
        return {"candidates": await asyncio.to_thread(deps.repo.brand_items, state["brand_key"], want_decaf)}

    async def rank(state: RecommendState) -> dict:
        profile, writer = state["profile"], get_stream_writer()
        tag_to_cat, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        passed = [i for i in state["candidates"] if passes(profile, i)[0]]
        top = mmr_top_k([(i, score_item(profile, i, tag_to_cat)) for i in passed], tag_to_cat, k=TOP_K)
        if not top:
            writer({"type": "empty", "reason": empty_reason(profile, state["candidates"])})
            return {"ranked": []}
        writer({"type": "cards", "cards": [card(i, s, template_explanation(i, profile, s, tag_ko)) for i, s in top]})
        return {"ranked": [{"item": i, "score": s} for i, s in top]}

    def fan_out(state: RecommendState):
        if not state.get("ranked"):
            return END
        return [Send("explain", {"item": r["item"], "score": r["score"], "profile": state["profile"]})
                for r in state["ranked"]]

    async def explain(payload: dict) -> dict:
        _, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        return {"explanations": [await explain_to_stream(deps, payload["item"], payload["profile"],
                                                         payload["score"], tag_ko)]}

    g = StateGraph(RecommendState)
    g.add_node("load", traced("recommend.load")(load))
    g.add_node("rank", traced("recommend.rank")(rank))
    g.add_node("explain", traced("recommend.explain")(explain))
    g.add_edge(START, "load")
    g.add_edge("load", "rank")
    g.add_conditional_edges("rank", fan_out, ["explain", END])
    g.add_edge("explain", END)
    return g.compile()
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/app/test_graph_recommend.py -v` → Expected: 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add app/graphs tests/app/fakes.py tests/app/test_graph_recommend.py
git commit -m "feat(graphs): recommend 그래프 — 필터→점수→다양성 top3→설명 병렬 fan-out(폴백)"
```

---

### Task 10: `analyze_bean` 그래프

**Files:**
- Create: `app/graphs/analyze_bean.py`, `tests/app/test_graph_analyze.py`

**Interfaces:**
- Consumes: Deps, Repo(`get_coffee`, `match_coffee`, `neighbors`, `fallback_neighbors`, `taxonomy`), core parse/predict/scoring/explain, `explain_to_stream`, `PARSE_BEAN_TASK`
- Produces: `build_analyze_graph(deps)`, 입력 `{"text": str | None, "coffee_id": int | None, "profile": Profile}`, 이벤트 `cards{cards:[card]}`(카드 1개, 예측이면 `evidence`·`n_neighbors` 포함, 조건 위반이면 `violation`) → 설명 이벤트

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_graph_analyze.py`

```python
from app.graphs.analyze_bean import build_analyze_graph
from app.models import Profile
from tests.app.fakes import fake_deps, run_events


def first_card(events):
    return next(e for e in events if e["type"] == "cards")["cards"][0]


def test_coffee_id_uses_db_data_without_embedding():
    deps = fake_deps()
    events = run_events(build_analyze_graph(deps), {"coffee_id": 2, "profile": Profile(body=4)})
    c = first_card(events)
    assert (c["source"], c["name"], c["confidence"]) == ("db", "Brazil Cerrado", "high")
    assert deps.calls["embed"] == 0 and deps.calls["json"] == 0
    assert any(e["type"] == "explain_done" for e in events)


def test_exact_name_match_skips_prediction():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps), {"text": "  brazil   cerrado ", "profile": Profile()}))
    assert c["source"] == "db" and deps.calls["embed"] == 0


def test_unknown_bean_is_predicted_from_neighbors_with_evidence():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["source"] == "predicted" and c["n_neighbors"] == 10
    assert c["evidence"][0].startswith("유사 원두 10개 중 10개에서 '레몬'")
    assert deps.calls["json"] == 0                     # rules found the origin, no LLM parse


def test_korean_text_without_origin_uses_llm_parse_and_still_answers():
    deps = fake_deps(parse={"origin_country": "Colombia", "is_decaf": True})
    events = run_events(build_analyze_graph(deps), {"text": "동네 로스터리 하우스 블렌드",
                                                     "profile": Profile(caffeine_rule="decaf_only")})
    c = first_card(events)
    assert deps.calls["json"] == 1 and c["is_decaf"] is True and c["violation"] is None
    assert any(e["type"] in ("explain_done", "explain_fallback") for e in events)


def test_parse_failure_and_embedding_failure_degrade_to_low_confidence():
    deps = fake_deps(json_fails=True, embed_fails=True)
    c = first_card(run_events(build_analyze_graph(deps), {"text": "하우스 블렌드", "profile": Profile()}))
    assert c["source"] == "predicted" and c["confidence"] == "low" and c["acidity"] is None
    deps2 = fake_deps(embed_fails=True)
    c2 = first_card(run_events(build_analyze_graph(deps2), {"text": "브라질 내추럴", "profile": Profile()}))
    assert c2["confidence"] == "low" and c2["n_neighbors"] == 4      # origin/process average fallback


def test_violation_is_reported_not_hidden():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps), {"coffee_id": 1,
                                                          "profile": Profile(caffeine_rule="decaf_only")}))
    assert c["violation"] == "디카페인이 아니에요"
    assert c["template"].startswith("주의: 디카페인이 아니에요.")
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_graph_analyze.py -v` → Expected: FAIL (import 오류)

- [ ] **Step 3: 구현** — `app/graphs/analyze_bean.py`

```python
import asyncio
from dataclasses import replace
from typing import TypedDict

import httpx
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.config import PARSE_BEAN_TASK
from app.core.explain import card, template_explanation
from app.core.parse import BeanParse, bean_parse_messages, merge_llm_parse, needs_llm_parse, parse_bean_text
from app.core.predict import item_from_prediction, predict_from_neighbors
from app.core.scoring import passes, score_item
from app.graphs.common import explain_to_stream
from app.models import Item, ParsedBean, Prediction, Profile
from app.tracing import traced
from pipeline.llm import LLMError

K_NEIGHBORS = 10


class AnalyzeState(TypedDict, total=False):
    text: str | None
    coffee_id: int | None
    profile: Profile
    parsed: ParsedBean
    item: Item | None
    prediction: Prediction | None
    score: float
    violation: str | None
    explanation: dict


def build_analyze_graph(deps):
    async def parse(state: AnalyzeState) -> dict:
        parsed = parse_bean_text(state.get("text") or "")
        if state.get("coffee_id") is None and needs_llm_parse(parsed):
            try:
                llm = await deps.chat_json(PARSE_BEAN_TASK, bean_parse_messages(parsed.text), BeanParse)
                parsed = merge_llm_parse(parsed, llm)
            except (LLMError, httpx.HTTPError):
                pass                                # rules only
        return {"parsed": parsed}

    async def match(state: AnalyzeState) -> dict:
        if state.get("coffee_id") is not None:
            return {"item": await asyncio.to_thread(deps.repo.get_coffee, state["coffee_id"])}
        return {"item": await asyncio.to_thread(deps.repo.match_coffee, state["parsed"].text)}

    def route(state: AnalyzeState) -> str:
        return "score" if state.get("item") else "predict"

    async def predict(state: AnalyzeState) -> dict:
        parsed = state["parsed"]
        _, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        degraded = False
        try:
            vec = await deps.embed(parsed.text)
            neighbors = await asyncio.to_thread(deps.repo.neighbors, vec, K_NEIGHBORS, parsed.origin_country,
                                                parsed.process)
        except (LLMError, httpx.HTTPError):
            degraded = True
            neighbors = await asyncio.to_thread(deps.repo.fallback_neighbors, parsed.origin_country, parsed.process)
        pred = predict_from_neighbors(neighbors, tag_ko)
        if degraded:
            pred = replace(pred, confidence="low")
        return {"item": item_from_prediction(parsed, pred), "prediction": pred}

    async def score(state: AnalyzeState) -> dict:
        profile, item = state["profile"], state["item"]
        tag_to_cat, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        ok, why = passes(profile, item)
        s = score_item(profile, item, tag_to_cat)
        get_stream_writer()({"type": "cards", "cards": [
            card(item, s, template_explanation(item, profile, s, tag_ko, why), why, state.get("prediction"))]})
        return {"score": s, "violation": why}

    async def explain(state: AnalyzeState) -> dict:
        _, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        return {"explanation": await explain_to_stream(deps, state["item"], state["profile"], state["score"], tag_ko,
                                                       state.get("prediction"), state.get("violation"))}

    g = StateGraph(AnalyzeState)
    for name, fn in (("parse", parse), ("match", match), ("predict", predict), ("score", score), ("explain", explain)):
        g.add_node(name, traced(f"analyze.{name}")(fn))
    g.add_edge(START, "parse")
    g.add_edge("parse", "match")
    g.add_conditional_edges("match", route, ["score", "predict"])
    g.add_edge("predict", "score")
    g.add_edge("score", "explain")
    g.add_edge("explain", END)
    return g.compile()
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_graph_analyze.py -v` → Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graphs/analyze_bean.py tests/app/test_graph_analyze.py
git commit -m "feat(graphs): analyze_bean 그래프 — 파싱(규칙→LLM)·DB 일치/유사 원두 예측 분기·임베딩 실패 대체"
```

---

### Task 11: `log_tasting` 그래프

**Files:**
- Create: `app/graphs/log_tasting.py`, `tests/app/test_graph_log.py`

**Interfaces:**
- Consumes: Deps, Repo(`taxonomy`, `save_tasting`, `save_profile`), `update_profile`, `NoteSignals`, `note_messages`, `PARSE_NOTE_TASK`
- Produces: `build_log_graph(deps)`, 입력 `{"user_id", "profile": Profile, "item": Item, "rating": int, "note": str | None, "target": dict, "persist_tasting": bool}` (`target`은 `{"coffee_id": int}` | `{"menu_item_id": int}` | `{"input_text": str, "predicted": dict}`), 결과 상태 `{"new_profile", "changes", "summary", "signals", "tasting_id"}` (`graph.ainvoke`로 사용)

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_graph_log.py`

```python
import asyncio

from app.graphs.log_tasting import build_log_graph
from app.models import Item, Profile
from tests.app.fakes import FakeRepo, fake_deps


def run(deps, **inputs):
    base = {"user_id": "u1", "profile": Profile(acidity=3), "note": None, "persist_tasting": True,
            "item": Item(key="coffee:1", name="x", source="db", acidity=5, tags=("lemon",), coffee_id=1),
            "rating": 5, "target": {"coffee_id": 1}}
    return asyncio.run(build_log_graph(deps).ainvoke(base | inputs))


def test_like_updates_profile_and_persists_with_history():
    repo = FakeRepo()
    out = run(fake_deps(repo))
    assert out["new_profile"].acidity == 4.0 and out["new_profile"].flavor_weights == {"fruity": 0.5}
    assert out["summary"] == "산미 선호 3.0→4.0 · '과일' 선호 ↑"
    assert repo.tastings[0]["coffee_id"] == 1 and repo.tastings[0]["rating"] == 5
    assert repo.history("u1")[0]["tasting_id"] == out["tasting_id"]


def test_note_signals_are_applied_and_stored():
    repo = FakeRepo()
    out = run(fake_deps(repo, parse={"acidity": "lower"}), rating=3, note="산미가 너무 셌어요")
    assert out["signals"] == {"acidity": "lower", "liked_flavors": [], "disliked_flavors": []}
    assert out["new_profile"].acidity == 2.5
    assert repo.tastings[0]["parsed_signals"] == out["signals"] and repo.tastings[0]["note"] == "산미가 너무 셌어요"


def test_note_parse_failure_keeps_rating_update():
    out = run(fake_deps(json_fails=True), note="음...")
    assert out["signals"] is None and out["new_profile"].acidity == 4.0


def test_onboarding_sample_does_not_create_tasting():
    repo = FakeRepo()
    run(fake_deps(repo), persist_tasting=False)
    assert repo.tastings == [] and repo.get_profile("u1").n_updates == 1


def test_neutral_rating_without_note_reports_no_change():
    out = run(fake_deps(), rating=3)
    assert out["summary"] == "취향 변화 없음"
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_graph_log.py -v` → Expected: FAIL (import 오류)

- [ ] **Step 3: 구현** — `app/graphs/log_tasting.py`

```python
import asyncio
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from app.config import PARSE_NOTE_TASK
from app.core.learning import update_profile
from app.core.parse import NoteSignals, note_messages
from app.models import Item, Profile
from app.tracing import traced
from pipeline.llm import LLMError


class LogState(TypedDict, total=False):
    user_id: str
    profile: Profile
    item: Item
    rating: int
    note: str | None
    target: dict
    persist_tasting: bool
    signals: dict | None
    new_profile: Profile
    changes: list[str]
    tasting_id: int | None
    summary: str


def build_log_graph(deps):
    async def parse_note(state: LogState) -> dict:
        note = (state.get("note") or "").strip()
        if not note:
            return {"signals": None}
        try:
            parsed = await deps.chat_json(PARSE_NOTE_TASK, note_messages(note), NoteSignals)
            return {"signals": parsed.model_dump(exclude_none=True)}
        except (LLMError, httpx.HTTPError):
            return {"signals": None}

    async def update(state: LogState) -> dict:
        tag_to_cat, _ = await asyncio.to_thread(deps.repo.taxonomy)
        new, changes = update_profile(state["profile"], state["item"], state["rating"], tag_to_cat,
                                      state.get("signals"))
        return {"new_profile": new, "changes": changes}

    async def persist(state: LogState) -> dict:
        tasting_id = None
        if state.get("persist_tasting", True):
            tasting_id = await asyncio.to_thread(
                lambda: deps.repo.save_tasting(state["user_id"], rating=state["rating"], note=state.get("note"),
                                               parsed_signals=state.get("signals"), **state["target"]))
        await asyncio.to_thread(deps.repo.save_profile, state["user_id"], state["new_profile"], tasting_id)
        return {"tasting_id": tasting_id}

    async def summarize(state: LogState) -> dict:
        return {"summary": " · ".join(state["changes"]) if state["changes"] else "취향 변화 없음"}

    g = StateGraph(LogState)
    for name, fn in (("parse_note", parse_note), ("update", update), ("persist", persist), ("summarize", summarize)):
        g.add_node(name, traced(f"log.{name}")(fn))
    g.add_edge(START, "parse_note")
    g.add_edge("parse_note", "update")
    g.add_edge("update", "persist")
    g.add_edge("persist", "summarize")
    g.add_edge("summarize", END)
    return g.compile()
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_graph_log.py -v && uv run pytest -q` → Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graphs/log_tasting.py tests/app/test_graph_log.py
git commit -m "feat(graphs): log_tasting 그래프 — 후기 신호 파싱·프로필 갱신·기록/이력 저장"
```

---

### Task 12: FastAPI — 게스트 세션, 엔드포인트, SSE

**Files:**
- Create: `app/api.py`, `tests/app/test_api.py`

**Interfaces:**
- Consumes: Repo(또는 FakeRepo), Deps, 3개 그래프, `app.config`, `app.tracing`, `PREFERENCE_CHIPS`, `CAFFEINE_RULES`
- Produces: `create_app(repo=None, deps=None, cookie_secure: bool | None = None) -> FastAPI` (인자 없으면 `Repo(pipeline.settings.DATABASE_URL)` + `default_deps`). 실행: `uv run uvicorn app.api:app --reload` (`app = create_app()`는 모듈 import 시 만들지 않고 `app.api:app`은 lazy 팩토리 `def app()`가 아니라 아래 `get_app()` + `uvicorn --factory`로 띄운다: `uv run uvicorn app.api:get_app --factory --reload`)
- 엔드포인트: `POST /session`, `GET /me`, `PUT /me/profile`, `GET /onboarding/samples`, `POST /onboarding/samples`, `GET /brands`, `GET /coffees/search`, `POST /recommend`(SSE), `POST /analyze`(SSE), `POST /tastings`, `GET /health`. SSE 형식: `event: <type>\ndata: <json>\n\n`, 마지막 `event: done`.

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_api.py`

```python
import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from tests.app.fakes import FakeRepo, fake_deps


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def client():
    repo = FakeRepo()
    c = TestClient(create_app(repo=repo, deps=fake_deps(repo), cookie_secure=False))
    c.repo = repo
    return c


def onboard(client, **profile):
    client.post("/session")
    body = {"caffeine_rule": "any", "milk_ok": True, "acidity": 4, "body": 2, "sweetness": 3,
            "flavor_likes": ["fruity"]} | profile
    assert client.put("/me/profile", json=body).status_code == 200


def test_session_creates_guest_and_reuses_cookie(client):
    r1 = client.post("/session")
    assert r1.json()["new"] is True and r1.json()["has_profile"] is False
    assert "httponly" in r1.headers["set-cookie"].lower()
    r2 = client.post("/session")
    assert r2.json() == {"user_id": r1.json()["user_id"], "new": False, "has_profile": False}


def test_garbage_cookie_is_401_then_session_recovers(client):
    client.cookies.set("cs_uid", "not-a-uuid")
    assert client.get("/me").status_code == 401
    assert client.post("/session").json()["new"] is True
    assert client.get("/me").status_code == 200


def test_profile_validation_and_me(client):
    client.post("/session")
    bad = client.put("/me/profile", json={"caffeine_rule": "none", "milk_ok": True, "acidity": 9, "body": 3,
                                          "sweetness": 3, "flavor_likes": ["bogus"]})
    assert bad.status_code == 422
    onboard(client, nickname="가연")
    me = client.get("/me").json()
    assert me["profile"]["flavor_weights"] == {"fruity": 0.5} and me["nickname"] == "가연"
    assert len(me["history"]) == 1


def test_recommend_requires_profile_then_streams(client):
    client.post("/session")
    assert client.post("/recommend", json={"brand_key": "brand:sb"}).status_code == 409
    onboard(client)
    r = client.post("/recommend", json={"brand_key": "brand:sb"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    types = [t for t, _ in events]
    assert types[0] == "cards" and types[-1] == "done" and types.count("explain_done") == 3


def test_analyze_validates_and_streams_without_review_text(client):
    onboard(client)
    assert client.post("/analyze", json={}).status_code == 422
    events = sse_events(client.post("/analyze", json={"text": "에티오피아 예가체프 워시드"}).text)
    card = events[0][1]["cards"][0]
    assert card["source"] == "predicted" and "evidence" in card
    assert "review" not in json.dumps(card) and "text" not in card


def test_tastings_flow_updates_profile(client):
    onboard(client)
    r = client.post("/tastings", json={"coffee_id": 1, "rating": 5, "note": ""})
    assert r.status_code == 200 and "산미 선호" in r.json()["summary"]
    assert client.get("/me").json()["tastings"][0]["rating"] == 5
    pred = client.post("/tastings", json={"input_text": "동네 블렌드", "rating": 2,
                                          "predicted": {"acidity": 3, "body": 3, "sweetness": 3, "tags": []}})
    assert pred.status_code == 200
    assert client.post("/tastings", json={"coffee_id": 999, "rating": 5}).status_code == 404
    assert client.post("/tastings", json={"coffee_id": 1, "menu_item_id": 10, "rating": 5}).status_code == 422
    assert client.post("/tastings", json={"coffee_id": 1, "rating": 6}).status_code == 422


def test_onboarding_samples_roundtrip(client):
    onboard(client)
    samples = client.get("/onboarding/samples").json()
    assert [s["coffee_id"] for s in samples] == [1, 2]
    r = client.post("/onboarding/samples", json=[{"coffee_id": 1, "liked": True}, {"coffee_id": 2, "liked": False}])
    assert r.status_code == 200 and r.json()["profile"]["n_updates"] == 2
    assert client.repo.tastings == []                  # samples don't create tastings


def test_catalog_endpoints(client):
    client.post("/session")
    assert client.get("/brands").json()[0]["key"] == "brand:sb"
    assert client.get("/coffees/search", params={"q": "brazil"}).json()[0]["name"] == "Brazil Cerrado"
    assert client.get("/health").json() == {"ok": True}
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_api.py -v` → Expected: FAIL (`app.api` 없음)

- [ ] **Step 3: 구현** — `app/api.py`

```python
import json
import logging
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app import config, tracing
from app.core.flavors import PREFERENCE_CHIPS
from app.graphs import default_deps
from app.graphs.analyze_bean import build_analyze_graph
from app.graphs.log_tasting import build_log_graph
from app.graphs.recommend import build_recommend_graph
from app.models import Item, Profile

log = logging.getLogger("coffee.api")


class ProfileIn(BaseModel):
    caffeine_rule: Literal["decaf_only", "low", "any"]
    milk_ok: bool
    acidity: float = Field(ge=1, le=5)
    body: float = Field(ge=1, le=5)
    sweetness: float = Field(ge=1, le=5)
    flavor_likes: list[Literal[PREFERENCE_CHIPS]] = Field(default_factory=list)  # type: ignore[valid-type]
    nickname: str | None = Field(default=None, max_length=30)


class SampleIn(BaseModel):
    coffee_id: int
    liked: bool


class RecommendIn(BaseModel):
    brand_key: str


class AnalyzeIn(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    coffee_id: int | None = None

    @model_validator(mode="after")
    def one_input(self):
        if not (self.text and self.text.strip()) and self.coffee_id is None:
            raise ValueError("text 또는 coffee_id가 필요해요")
        return self


class PredictedIn(BaseModel):
    acidity: float | None = None
    body: float | None = None
    sweetness: float | None = None
    tags: list[str] = Field(default_factory=list)
    is_decaf: bool = False


class TastingIn(BaseModel):
    coffee_id: int | None = None
    menu_item_id: int | None = None
    input_text: str | None = Field(default=None, max_length=300)
    predicted: PredictedIn | None = None
    order_decaf: bool = False
    rating: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def exactly_one_target(self):
        if sum(x is not None for x in (self.coffee_id, self.menu_item_id, self.input_text)) != 1:
            raise ValueError("coffee_id, menu_item_id, input_text 중 하나만 보내 주세요")
        return self


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def create_app(repo=None, deps=None, cookie_secure: bool | None = None) -> FastAPI:
    if repo is None:
        from app.repo import Repo
        from pipeline import settings
        repo = Repo(settings.DATABASE_URL)
    deps = deps or default_deps(repo)
    secure = config.cookie_secure() if cookie_secure is None else cookie_secure
    graphs = {"recommend": build_recommend_graph(deps), "analyze": build_analyze_graph(deps),
              "log": build_log_graph(deps)}

    app = FastAPI(title="Coffee Sommelier API", version="0.2.0")
    app.add_middleware(CORSMiddleware, allow_origins=config.allowed_origins(), allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    def current_user(request: Request) -> str:
        uid = request.cookies.get(config.COOKIE_NAME)
        if not repo.user_exists(uid):
            raise HTTPException(401, "세션이 없어요. /session을 먼저 호출하세요")
        return uid

    def profile_of(uid: str) -> Profile:
        p = repo.get_profile(uid)
        if p is None:
            raise HTTPException(409, "온보딩이 필요해요")
        return p

    def stream(graph, inputs: dict, name: str) -> StreamingResponse:
        async def gen():
            with tracing.span(name):
                try:
                    async for event in graph.astream(inputs, stream_mode="custom"):
                        yield _sse(event["type"], event)
                except Exception:           # never leak a traceback into the stream
                    log.exception("%s stream failed", name)
                    yield _sse("error", {"message": "처리 중 오류가 발생했어요"})
            tracing.flush()
            yield _sse("done", {})
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/session")
    def session(request: Request, response: Response):
        uid = request.cookies.get(config.COOKIE_NAME)
        new = not repo.user_exists(uid)
        if new:
            uid = repo.create_user()
            response.set_cookie(config.COOKIE_NAME, uid, max_age=config.COOKIE_MAX_AGE, httponly=True,
                                secure=secure, samesite=config.cookie_samesite())
        return {"user_id": uid, "new": new, "has_profile": repo.get_profile(uid) is not None}

    @app.get("/me")
    def me(uid: str = Depends(current_user)):
        p = repo.get_profile(uid)
        return {"user_id": uid, "nickname": repo.get_nickname(uid), "profile": p.to_dict() if p else None,
                "history": repo.history(uid), "tastings": repo.recent_tastings(uid)}

    @app.put("/me/profile")
    def put_profile(body: ProfileIn, uid: str = Depends(current_user)):
        old = repo.get_profile(uid)
        weights = dict(old.flavor_weights) if old else {}
        weights.update({c: max(weights.get(c, 0.0), 0.5) for c in body.flavor_likes})
        p = Profile(caffeine_rule=body.caffeine_rule, milk_ok=body.milk_ok, acidity=body.acidity, body=body.body,
                    sweetness=body.sweetness, flavor_weights=weights, n_updates=old.n_updates if old else 0)
        repo.save_profile(uid, p)
        if body.nickname is not None:
            repo.set_nickname(uid, body.nickname.strip() or None)
        return {"profile": p.to_dict()}

    @app.get("/onboarding/samples")
    def samples(uid: str = Depends(current_user)):
        return repo.sample_coffees()

    @app.post("/onboarding/samples")
    async def post_samples(body: list[SampleIn], uid: str = Depends(current_user)):
        p = profile_of(uid)
        for s in body:
            item = repo.get_coffee(s.coffee_id)
            if item is None:
                raise HTTPException(404, f"원두 {s.coffee_id}를 찾을 수 없어요")
            out = await graphs["log"].ainvoke({"user_id": uid, "profile": p, "item": item,
                                               "rating": 5 if s.liked else 1, "note": None, "target": {},
                                               "persist_tasting": False})
            p = out["new_profile"]
        return {"profile": p.to_dict()}

    @app.get("/brands")
    def brands(uid: str = Depends(current_user)):
        return repo.list_brands()

    @app.get("/coffees/search")
    def search(q: str = "", uid: str = Depends(current_user)):
        return repo.search_coffees(q)

    @app.post("/recommend")
    def recommend(body: RecommendIn, uid: str = Depends(current_user)):
        return stream(graphs["recommend"], {"brand_key": body.brand_key, "profile": profile_of(uid)}, "recommend")

    @app.post("/analyze")
    def analyze(body: AnalyzeIn, uid: str = Depends(current_user)):
        return stream(graphs["analyze"], {"text": body.text, "coffee_id": body.coffee_id,
                                          "profile": profile_of(uid)}, "analyze")

    @app.post("/tastings")
    async def tastings(body: TastingIn, uid: str = Depends(current_user)):
        p = profile_of(uid)
        want_decaf = body.order_decaf or p.caffeine_rule in ("decaf_only", "low")
        if body.coffee_id is not None:
            item, target = repo.get_coffee(body.coffee_id), {"coffee_id": body.coffee_id}
        elif body.menu_item_id is not None:
            item, target = repo.get_menu_item(body.menu_item_id, want_decaf), {"menu_item_id": body.menu_item_id}
        else:
            pred = body.predicted or PredictedIn()
            item = Item(key="input", name=body.input_text, source="predicted", acidity=pred.acidity, body=pred.body,
                        sweetness=pred.sweetness, tags=tuple(pred.tags), is_decaf=pred.is_decaf)
            target = {"input_text": body.input_text, "predicted": pred.model_dump()}
        if item is None:
            raise HTTPException(404, "대상을 찾을 수 없어요")
        with tracing.span("log_tasting"):
            out = await graphs["log"].ainvoke({"user_id": uid, "profile": p, "item": item, "rating": body.rating,
                                               "note": body.note, "target": target, "persist_tasting": True})
        tracing.flush()
        return {"summary": out["summary"], "changes": out["changes"], "profile": out["new_profile"].to_dict()}

    return app


def get_app() -> FastAPI:
    """uvicorn factory: `uv run uvicorn app.api:get_app --factory`"""
    return create_app()
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_api.py -v && uv run pytest -q` → Expected: 전부 PASS

- [ ] **Step 5: 실서버 스모크 (실DB + 로컬/NVIDIA LLM)**

Run(백그라운드): `COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --port 8000`
그다음:
```bash
curl -s -c /tmp/cj -X POST localhost:8000/session
curl -s -b /tmp/cj -X PUT localhost:8000/me/profile -H 'content-type: application/json' \
  -d '{"caffeine_rule":"decaf_only","milk_ok":true,"acidity":4.5,"body":2.5,"sweetness":3,"flavor_likes":["fruity","floral"]}'
curl -s -N -b /tmp/cj -X POST localhost:8000/recommend -H 'content-type: application/json' -d '{"brand_key":"brand:starbucks"}'
curl -s -N -b /tmp/cj -X POST localhost:8000/analyze -H 'content-type: application/json' -d '{"text":"에티오피아 예가체프 워시드 디카페인"}'
```
Expected: `cards` 이벤트가 즉시, 이어서 `explain_delta` 여러 개와 `explain_done`(또는 `explain_fallback`), 마지막 `done`. 스타벅스 카드에는 `order_decaf: true`. 결과(시간 포함)를 보고서에 기록하고 서버를 종료한다.

- [ ] **Step 6: Commit**

```bash
git add app/api.py tests/app/test_api.py
git commit -m "feat(api): FastAPI — 게스트 쿠키 세션, 온보딩·추천·분석·기록 엔드포인트, SSE 스트리밍"
```

---

### Task 13: 평가 명령 (`python -m app.eval`)

**Files:**
- Create: `app/eval.py`, `app/__main__.py`는 만들지 않는다(모듈 실행은 `python -m app.eval`), `tests/app/test_eval.py`

**Interfaces:**
- Consumes: Repo(평가 헬퍼), core(`passes`, `score_item`, `mmr_top_k`, `predict_from_neighbors`, `simulate_convergence`), `app.llm.astream_text`, `app.core.explain.explain_messages`
- Produces: `PERSONAS: list[tuple[str, Profile]]`, `violation_rate(repo) -> dict`, `independent_ok(profile, item, raw: dict | None, brand_decaf_available: bool) -> bool`, `loo_accuracy(repo, n=200, seed=42) -> dict`, `convergence(repo, users=200, seed=1) -> dict`, `bench(repo) -> dict`, CLI `python -m app.eval [violations|loo|convergence|bench|all]` → `data/eval/phase2_<name>.json`

- [ ] **Step 1: 실패하는 테스트** — `tests/app/test_eval.py`

```python
from app.eval import independent_ok, violation_rate
from app.models import Item, Profile
from tests.app.fakes import FakeRepo


def test_independent_check_uses_raw_fields():
    decaf = Profile(caffeine_rule="decaf_only", milk_ok=False)
    it = Item(key="menu:1", name="아메리카노", source="brand_bean", menu_item_id=1)
    assert independent_ok(decaf, it, {"name": "아메리카노", "is_decaf": False, "decaf_option": True,
                                      "caffeine_mg": 150}, True)
    assert not independent_ok(decaf, it, {"name": "아메리카노", "is_decaf": False, "decaf_option": False,
                                          "caffeine_mg": 150}, True)
    assert not independent_ok(decaf, it, {"name": "카페 라떼", "is_decaf": True, "decaf_option": False,
                                          "caffeine_mg": 10}, True)
    synthetic = Item(key="brand:x:아메리카노", name="아메리카노", source="brand_bean")
    assert independent_ok(decaf, synthetic, None, True) and not independent_ok(decaf, synthetic, None, False)


def test_violation_rate_on_fake_catalog_is_zero():
    class Repo(FakeRepo):
        def raw_menu(self, menu_item_id):
            for items in self.menu.values():
                for i in items:
                    if i.menu_item_id == menu_item_id:
                        return {"name": i.name, "is_decaf": i.is_decaf, "decaf_option": i.decaf_option,
                                "caffeine_mg": i.caffeine_mg}
            return None
    r = violation_rate(Repo())
    assert r["checked"] > 0 and r["violations"] == 0 and r["rate"] == 0.0
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/app/test_eval.py -v` → Expected: FAIL (`app.eval` 없음)

- [ ] **Step 3: 구현** — `app/eval.py`

```python
"""Phase 2 evaluation: hard-constraint violations, leave-one-out prediction accuracy, learning convergence,
and latency benchmarks for the ADRs. Results go to data/eval/phase2_<name>.json."""
import asyncio
import json
import sys
import time

from app.core.explain import explain_messages
from app.core.predict import predict_from_neighbors
from app.core.scoring import mmr_top_k, passes, score_item
from app.core.simulate import simulate_convergence
from app.models import ATTRS, Item, Profile
from pipeline import settings

PERSONAS = [
    ("디카페인+산미", Profile(caffeine_rule="decaf_only", milk_ok=True, acidity=4.5, body=2.5, sweetness=3,
                          flavor_weights={"fruity": 0.6, "floral": 0.5})),
    ("저카페인+우유X", Profile(caffeine_rule="low", milk_ok=False, acidity=2, body=4, sweetness=3,
                           flavor_weights={"nutty/cocoa": 0.6})),
    ("제한없음", Profile()),
    ("디카페인+우유X+단맛", Profile(caffeine_rule="decaf_only", milk_ok=False, acidity=2, body=3, sweetness=4.5,
                              flavor_weights={"sweet": 0.6})),
]
# Deliberately NOT the scoring module's list: an independent, raw-field check.
MILK_MARKERS = ("라떼", "우유", "밀크", "크림", "카푸치노", "플랫화이트", "모카", "프라푸치노", "latte", "milk", "cream",
                "cappuccino", "mocha", "frappuccino")


def independent_ok(profile: Profile, item: Item, raw: dict | None, brand_decaf_available: bool) -> bool:
    name = (raw["name"] if raw else item.name).lower()
    decaf_capable = (raw["is_decaf"] or raw["decaf_option"]) if raw else brand_decaf_available
    caffeine = raw["caffeine_mg"] if raw else None
    if profile.caffeine_rule == "decaf_only" and not decaf_capable:
        return False
    if profile.caffeine_rule == "low" and not (decaf_capable or (caffeine is not None and caffeine <= 100)):
        return False
    if not profile.milk_ok and any(m in name for m in MILK_MARKERS):
        return False
    return True


def violation_rate(repo) -> dict:
    tag_to_cat, _ = repo.taxonomy()
    checked = violations = 0
    details = []
    for label, p in PERSONAS:
        for b in repo.list_brands():
            items = repo.brand_items(b["key"], p.caffeine_rule in ("decaf_only", "low"))
            top = mmr_top_k([(i, score_item(p, i, tag_to_cat)) for i in items if passes(p, i)[0]], tag_to_cat)
            for i, _ in top:
                checked += 1
                raw = repo.raw_menu(i.menu_item_id) if i.menu_item_id is not None else None
                if not independent_ok(p, i, raw, b["decaf_available"]):
                    violations += 1
                    details.append({"persona": label, "brand": b["key"], "item": i.name})
    return {"checked": checked, "violations": violations, "rate": violations / checked if checked else 0.0,
            "details": details}


def loo_accuracy(repo, n: int = 200, seed: int = 42) -> dict:
    stats = {a: {"n": 0, "exact": 0, "within1": 0} for a in ATTRS}
    by_conf: dict[str, dict] = {}
    for cid in repo.random_coffee_ids_for_loo(n, seed):
        truth = repo.get_coffee(cid)
        pred = predict_from_neighbors(repo.neighbors(repo.coffee_embedding(cid), k=10, exclude_id=cid))
        for a in ATTRS:
            t, v = truth.attr(a), getattr(pred, a)
            if t is None or v is None:
                continue
            s = stats[a]
            s["n"] += 1
            s["exact"] += round(v) == t
            s["within1"] += abs(v - t) <= 1
        if truth.acidity is not None and pred.acidity is not None:
            c = by_conf.setdefault(pred.confidence, {"n": 0, "within1": 0})
            c["n"] += 1
            c["within1"] += abs(pred.acidity - truth.acidity) <= 1
    rate = lambda d, k: round(d[k] / d["n"], 4) if d["n"] else None  # noqa: E731
    return {"n": n, "seed": seed, "embedding_model": settings.load_config("models.yaml")["tasks"]["embed"]["model"],
            **{a: {"n": s["n"], "exact": rate(s, "exact"), "within1": rate(s, "within1")} for a, s in stats.items()},
            "acidity_within1_by_confidence": {k: {"n": v["n"], "within1": rate(v, "within1")} for k, v in by_conf.items()}}


def convergence(repo, users: int = 200, seed: int = 1) -> dict:
    tag_to_cat, _ = repo.taxonomy()
    return simulate_convergence(repo.random_coffees_with_attrs(400, seed), tag_to_cat, users=users, seed=seed)


def bench(repo) -> dict:
    """Real-LLM latency numbers for the ADRs: sequential vs parallel explanations, time to first token."""
    from app import llm
    from app.config import EXPLAIN_TASK

    items = repo.random_coffees_with_attrs(3, 7)
    profile = PERSONAS[0][1]
    msgs = [explain_messages(i, profile, 0.8) for i in items]

    async def one(m):
        start, first = time.perf_counter(), None
        async for _ in llm.astream_text(EXPLAIN_TASK, m):
            first = first or time.perf_counter() - start
        return first, time.perf_counter() - start

    async def run():
        t0 = time.perf_counter()
        seq = [await one(m) for m in msgs]
        sequential = time.perf_counter() - t0
        t1 = time.perf_counter()
        par = await asyncio.gather(*(one(m) for m in msgs))
        parallel = time.perf_counter() - t1
        return seq, sequential, par, parallel

    seq, sequential, par, parallel = asyncio.run(run())
    return {"model": settings.load_config("models.yaml")["tasks"][EXPLAIN_TASK]["model"],
            "sequential_total_s": round(sequential, 2), "parallel_total_s": round(parallel, 2),
            "first_token_s": [round(f, 2) for f, _ in seq],
            "full_answer_s": [round(t, 2) for _, t in seq]}


def main(argv: list[str]) -> int:
    from app.repo import Repo
    names = argv or ["all"]
    targets = ["violations", "loo", "convergence", "bench"] if names == ["all"] else names
    repo = Repo(settings.DATABASE_URL)
    fns = {"violations": violation_rate, "loo": loo_accuracy, "convergence": convergence, "bench": bench}
    try:
        for name in targets:
            result = fns[name](repo)
            out = settings.EVAL_DIR / f"phase2_{name}.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{name}] {json.dumps(result, ensure_ascii=False)[:400]}")
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/app/test_eval.py -v && uv run pytest -q` → Expected: 전부 PASS

- [ ] **Step 5: 실데이터 평가 실행**

Run: `uv run python -m app.eval violations loo convergence`
Expected: `phase2_violations.json`의 `rate` **0.0** (0이 아니면 `details`를 보고 필터 버그를 고친 뒤 재실행 — 이 스텝은 0이 될 때까지 완료가 아니다), `phase2_loo.json`의 산미·바디 `within1` 값, `phase2_convergence.json`의 `improvement > 0`. 세 값을 보고서에 기록.

Run: `uv run python -m app.eval bench` (NVIDIA 키 필요, 약 1~2분)
Expected: `phase2_bench.json`에 `parallel_total_s < sequential_total_s`. 결과를 보고서에 기록.

- [ ] **Step 6: Commit**

```bash
git add app/eval.py tests/app/test_eval.py data/eval/phase2_*.json
git commit -m "feat(eval): 2단계 평가 — 조건 위반율, 원두 예측 leave-one-out, 학습 수렴, 설명 지연 벤치"
```

---

### Task 14: ADR 2건 + README 2단계 백엔드 섹션

**Files:**
- Create: `docs/adr/0001-fastapi.md`, `docs/adr/0002-langgraph.md`, `docs/graphs.md`
- Modify: `README.md` (1단계 결과 섹션 뒤에 "2단계 백엔드" 섹션 추가, 로드맵 표의 2단계 상태를 "진행 중 — 백엔드 완료"로)

**Interfaces:**
- Consumes: `data/eval/phase2_*.json`, Task 7 모델 선정 표, Task 12 스모크 결과

- [ ] **Step 1: 그래프 다이어그램 생성** — `docs/graphs.md`를 아래 스크립트 출력으로 만든다:

```bash
uv run python -c "
from app.graphs.recommend import build_recommend_graph
from app.graphs.analyze_bean import build_analyze_graph
from app.graphs.log_tasting import build_log_graph
from tests.app.fakes import fake_deps
d = fake_deps()
print('# LangGraph 그래프 (자동 생성: CompiledGraph.get_graph().draw_mermaid())\n')
for title, g in (('recommend', build_recommend_graph(d)), ('analyze_bean', build_analyze_graph(d)), ('log_tasting', build_log_graph(d))):
    print(f'## {title}\n\n\`\`\`mermaid\n{g.get_graph().draw_mermaid()}\`\`\`\n')
" > docs/graphs.md
```
Expected: 파일에 mermaid 블록 3개. GitHub에서 렌더링된다.

- [ ] **Step 2: `docs/adr/0001-fastapi.md`** — 아래 전문을 쓰고, "실측" 표의 값은 `data/eval/phase2_bench.json`에서 그대로 옮긴다(값이 없으면 표 행을 지우지 말고 "측정 실패: 사유"로 적는다).

```markdown
# ADR 0001 — API 프레임워크: FastAPI

- 상태: 채택 (2026-09-26)

## 맥락
추천 API는 (1) 결과 카드를 즉시 보내고 LLM 설명을 토큰 단위로 스트리밍(SSE)해야 하고, (2) 카드 3장의 설명 LLM 호출을 동시에 돌려야 하며,
(3) 1단계 데이터 모델이 이미 Pydantic이고, (4) LangGraph 그래프가 async다. "REST API"는 설계 방식이고, 여기서의 선택지는 그 REST API를 구현할 파이썬 프레임워크다.

## 대안
| 대안 | 장점 | 단점 |
|---|---|---|
| **FastAPI** | 네이티브 async, Pydantic 입출력 검증, `/docs` 자동 문서, StreamingResponse로 SSE | 비교적 젊은 생태계 |
| Flask | 단순, 익숙함 | async가 부가 기능, 검증·문서를 별도 라이브러리로 |
| Django REST Framework | 인증·어드민 등 풀스택 | API 서버만 필요한 이 프로젝트엔 무겁고 async 스트리밍이 번거로움 |

## 결정
FastAPI. 요청/응답 모델은 Pydantic(`app/api.py`의 `ProfileIn`, `TastingIn` 등), 스트리밍은 `StreamingResponse(media_type="text/event-stream")`.

## 실측 (data/eval/phase2_bench.json)
| 항목 | 값 |
|---|---|
| 설명 모델 | (bench `model`) |
| 설명 3개 순차 생성 총 시간 | (sequential_total_s) 초 |
| 설명 3개 병렬 생성 총 시간 | (parallel_total_s) 초 |
| 첫 토큰까지(스트리밍) | (first_token_s 평균) 초 |
| 전체 답변까지(비스트리밍이었다면 이만큼 기다림) | (full_answer_s 평균) 초 |

## 결과
- 병렬 fan-out과 스트리밍으로 사용자가 기다리는 체감 시간이 줄었다(위 표).
- 동기 psycopg를 스레드로 호출(Windows 이벤트 루프 제약) — DB 호출이 짧아 병목이 아님.
```

- [ ] **Step 3: `docs/adr/0002-langgraph.md`**

```markdown
# ADR 0002 — AI 흐름 오케스트레이션: LangGraph (LangChain 미사용)

- 상태: 채택 (2026-09-26)

## 맥락
2단계의 AI 흐름에는 분기("DB에 있는 원두인가?"), 폴백(LLM 실패 → 템플릿, 임베딩 실패 → 산지·가공 평균), 병렬(설명 3개), 단계별 트레이싱·스트리밍이 있다.
4단계에서는 이 흐름들을 도구로 쓰는 멀티턴 에이전트가 필요하다.

## 대안
| 요구 | LangChain 체인(LCEL) | LangGraph | 직접 구현(asyncio) |
|---|---|---|---|
| 조건 분기 | 라우팅 체인으로 우회 | `add_conditional_edges` | if문 |
| 실패 폴백 | 예외 처리 별도 | 노드 안의 폴백 + 그래프에 표현 | try/except |
| 병렬 fan-out | 가능하나 번거로움 | `Send` 로 기본 지원 | `asyncio.gather` |
| 노드별 스트리밍·트레이싱 | 제한적 | `get_stream_writer`, 노드 단위 span | 직접 구현 |
| 구조 시각화 | 없음 | `draw_mermaid()` 자동 생성 | 없음 |
| 4단계 에이전트 확장 | LangGraph로 이전 권장 | 같은 그래프를 도구로 재사용 + checkpointer | 처음부터 |

## 결정
LangGraph 1.2만 쓴다. LLM 호출은 1단계 자체 클라이언트(`pipeline/llm.py`, `app/llm.py`)를 그대로 써서 LangChain 의존성을 들이지 않는다.
단순 조회(브랜드 목록·검색·내 정보)는 그래프 없이 엔드포인트에서 바로 처리한다 — 그래프는 분기·폴백·병렬이 있는 곳에만.

## 증거
- 구조: `docs/graphs.md` (자동 생성 다이어그램 3개)
- 폴백 동작: `tests/app/test_graph_recommend.py::test_one_failed_explanation_falls_back_others_stream`,
  `tests/app/test_graph_analyze.py::test_parse_failure_and_embedding_failure_degrade_to_low_confidence`
- 병렬 효과: ADR 0001 실측 표(순차 vs 병렬)
- 노드별 시간: Langfuse 트레이스(키 설정 시). 스크린샷은 배포 후 README에 추가.

## 결과
- 노드는 `app/core/` 순수 함수의 얇은 래퍼라 로직은 그래프 없이 테스트된다.
- 4단계에서 recommend / analyze_bean / log_tasting 을 에이전트의 도구로 노출하고 checkpointer로 대화 메모리를 붙인다.
```

- [ ] **Step 4: README 2단계 섹션 추가** — `README.md`의 "## 빠른 시작" 바로 앞에 아래 섹션을 넣고, 괄호 안 값은 `data/eval/phase2_*.json`에서 옮긴다. 로드맵 표의 `2. 추천 + 기록` 행 상태를 `🚧 백엔드 완료 (화면·배포 진행 예정)`으로 바꾼다.

```markdown
## 2단계 백엔드 (추천 + 기록)

- **구조:** FastAPI + LangGraph 그래프 3개(`recommend`, `analyze_bean`, `log_tasting`) — [그래프 다이어그램](docs/graphs.md), 결정 근거 [ADR 0001](docs/adr/0001-fastapi.md) · [ADR 0002](docs/adr/0002-langgraph.md)
- **추천 방식:** 하드 조건 필터(카페인·우유) → 취향 적합도(속성 0.6 + 향미 0.4) → MMR 다양성 top3 → 설명 3개 병렬 스트리밍(실패 시 템플릿)
- **처음 보는 원두:** 규칙 파싱(불확실하면 LLM) → DB에 있으면 실측, 없으면 유사 원두 10개 가중 평균으로 예측 + 신뢰도 + 근거 요약
- **학습:** 별점(좋음 끌어당김 / 별로 밀어냄, 학습률 1/(n+2)) + 한 줄 후기에서 LLM이 뽑은 신호("산미 너무 셈" → 산미 −0.5)

| 평가 (`python -m app.eval`) | 결과 |
|---|---|
| 조건 위반율 (페르소나 4 × 브랜드 10, top3) | (rate) — (checked)건 중 (violations)건 |
| 원두 예측 leave-one-out, 산미 ±1 이내 | (acidity.within1) (n=(acidity.n)) |
| 원두 예측 leave-one-out, 바디 ±1 이내 | (body.within1) |
| 학습 수렴: 모의 사용자 10회 기록 후 프로필 오차 | (mae_by_step[0]) → (mae_by_step[10]) |
| 설명 3개 순차 vs 병렬 | (sequential_total_s)초 → (parallel_total_s)초 |

API 실행: `COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --reload` → http://localhost:8000/docs
```

- [ ] **Step 5: 전체 테스트** — Run: `uv run pytest -q` → Expected: 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add docs/adr docs/graphs.md README.md
git commit -m "docs: ADR(FastAPI·LangGraph) + 실측, 그래프 다이어그램, README 2단계 백엔드 결과"
```
