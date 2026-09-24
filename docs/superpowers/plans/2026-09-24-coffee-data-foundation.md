# Coffee Sommelier 1단계: 데이터 기반 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 커피 지식베이스(원두·리뷰·프랜차이즈 메뉴·SCA 택소노미)를 수집 → 정규화 → 구조화 → 임베딩 → Postgres/pgvector 적재까지 한 명령으로 재현 가능하게 만든다.

**Architecture:** `pipeline/` 패키지가 5단계(collect, normalize, enrich, embed, load)를 가진다. 각 단계는 앞 단계의 파일 산출물(`data/raw` → `data/normalized` → `data/enriched` → `data/embedded`)만 읽으므로 독립 재실행된다. LLM/임베딩은 OpenAI 호환 HTTP 클라이언트 하나로 로컬 Ollama와 NVIDIA 엔드포인트를 모두 다루고, 작업별 모델은 `config/models.yaml`에서 고른다.

**Tech Stack:** Python 3.12, uv, httpx, pydantic v2, pandas, BeautifulSoup(lxml), psycopg 3, pgvector(Postgres 17, Docker), PyYAML, kaggle, pytest. 로컬 Ollama(`qwen3.5:9b`, `bge-m3`), NVIDIA API 카탈로그.

**Spec:** `docs/superpowers/specs/2026-09-24-coffee-sommelier-design.md` (섹션 3 = 이 계획의 범위)

## Global Constraints

- Python `>=3.12`, 의존성 관리는 `uv` (`uv sync`, `uv run ...`). 프로젝트는 패키지로 빌드하지 않는다(`[tool.uv] package = false`).
- DB: `pgvector/pgvector:pg17` 이미지, 접속 `postgresql://coffee:coffee@localhost:5432/coffee`. 테스트 DB는 `coffee_test`.
- 임베딩 차원 `1024` (bge-m3). `coffees.embedding vector(1024)`.
- 모든 원격 LLM 호출: 타임아웃 + 로컬 폴백, 빈 `content`는 실패, 429는 지수 백오프, 이미지는 base64 data URL (Vision은 3단계지만 클라이언트 규칙은 여기서 만든다).
- 크롤링: robots.txt 준수, 호스트별 요청 간 1초 지연, User-Agent `CoffeeSommelierBot/0.1 (+portfolio project)`. coffeereview.com, 투썸, 컴포즈는 직접 수집하지 않는다.
- 모든 레코드에 `source`, `source_url`, `collected_at`(스냅샷 날짜 `YYYY-MM-DD`) 기록.
- 리뷰 원문은 RAG 근거 전용 — 1단계에는 사용자 화면이 없지만 `reviews` 테이블은 이 용도로만 설계한다.
- 산미/바디/단맛 점수는 정수 1~5. 소스 원점수는 **소스 내 백분위 5분위**로 변환한다.
- 원시·중간 산출물(`data/raw|normalized|enriched|embedded|reports`)은 git에 넣지 않는다. `data/curated/`와 `data/eval/`은 커밋한다.
- 테스트는 네트워크 없이 돈다. DB가 필요한 테스트는 `@pytest.mark.db`이고 Postgres가 없으면 skip.
- 셸 명령은 레포 루트(`C:\Users\pc\orca\workspaces\wine-sommelier_rag\hawksbill`)에서 실행한다.

## 범위 메모 (스펙 대비)

- 프랜차이즈 **음료 단위** 자동 수집은 스타벅스·메가·빽다방 3곳(조사에서 수집 용이로 확인된 곳, 약 500행)만 한다. 폴바셋·할리스·커피빈·이디야·투썸·컴포즈는 `data/curated/brands.yaml`의 브랜드 행으로 다룬다. 음료 단위 확장은 2단계에서 필요할 때 수집기를 추가한다.
- 국내 로스터리는 **Shopify 수집기 + `config/sources.yaml`의 도메인 목록**으로 다룬다. 블루보틀 코리아가 첫 항목이며, 다른 로스터리는 robots.txt 확인 후 목록에 한 줄 추가하면 된다.
- `enrich_log`는 enrich 단계가 파일 캐시(`data/enriched/cache.jsonl`)로 기록하고, load 단계가 테이블에 적재한다(단계 간 파일 경계 유지).

## File Structure

```
pyproject.toml                 의존성, pytest 설정
docker-compose.yml             pgvector DB
.env.example                   키 이름만
config/models.yaml             공급자·작업별 모델
config/sources.yaml            Shopify 도메인 목록
data/curated/brands.yaml       프랜차이즈 브랜드 수기 정리
data/curated/sca_ko.yaml       SCA 1·2단계 한국어 매핑
data/eval/gold_enrich.csv      (Task 12에서 생성, 사람이 라벨링)
db/schema.sql                  테이블·인덱스
pipeline/
  __init__.py
  __main__.py                  CLI
  settings.py                  경로·환경변수·설정 로딩
  records.py                   pydantic 레코드 + JSONL 입출력
  rules.py                     정규화 규칙(디카페인·산지·가공·로스팅·5분위)
  http.py                      PoliteClient (robots, 지연)
  collect/__init__.py          Collector 프로토콜, run_collect, latest_snapshot
  collect/datasets.py          CQI, RoasterDB, SCA, Kaggle
  collect/web.py               스타벅스, 메가, 빽다방, Shopify
  collect/registry.py          ALL_COLLECTORS
  normalize/__init__.py        Normalized, run_normalize
  normalize/datasets.py        coffeereview, cqi, roasterdb, sca
  normalize/menus.py           starbucks, mega, paik, shopify, brands
  llm.py                       Target, LLMClient, Embedder, client_for, embedder_for
  enrich.py                    규칙 + LLM 보강, 캐시
  embed.py                     임베딩 텍스트 생성 + 캐시
  db.py                        connect, apply_schema, reset_tables
  load.py                      적재
  report.py                    품질 리포트
  query.py                     유사도 질의
  gold.py                      정답셋 샘플링·채점
tests/
  conftest.py                  db_conn 픽스처
  test_settings.py test_rules.py test_http.py test_collect.py
  test_normalize_datasets.py test_normalize_menus.py test_llm.py
  test_enrich.py test_embed.py test_db_load.py test_cli.py test_gold.py
```

---

### Task 1: 레포 전환 + 프로젝트 골격

**Files:**
- Delete: `app.py`, `wine_logic.py`, `data.ipynb`
- Create: `pyproject.toml`, `docker-compose.yml`, `.env.example`, `pipeline/__init__.py`, `pipeline/settings.py`, `tests/test_settings.py`
- Modify: `.gitignore` (끝에 추가)

**Interfaces:**
- Produces: `pipeline.settings` — `ROOT: Path`, `DATA_DIR`, `RAW_DIR`, `NORMALIZED_DIR`, `ENRICHED_DIR`, `EMBEDDED_DIR`, `REPORTS_DIR`, `CURATED_DIR`, `EVAL_DIR`, `CONFIG_DIR: Path`, `DATABASE_URL: str`, `load_config(name: str) -> dict`

- [ ] **Step 1: 와인 버전 태그 후 와인 코드 제거**

```bash
git tag wine-v1 3b8fb1e
git rm app.py wine_logic.py data.ipynb
```

- [ ] **Step 2: `pyproject.toml` 작성**

```toml
[project]
name = "coffee-sommelier"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.7",
  "python-dotenv>=1.0",
  "pyyaml>=6.0",
  "pandas>=2.2",
  "numpy>=1.26",
  "beautifulsoup4>=4.12",
  "lxml>=5.2",
  "psycopg[binary]>=3.2",
  "kaggle>=1.6",
]

[dependency-groups]
dev = ["pytest>=8"]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["db: needs running Postgres (docker compose up -d db)"]
```

- [ ] **Step 3: `docker-compose.yml` 작성**

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_USER: coffee
      POSTGRES_PASSWORD: coffee
      POSTGRES_DB: coffee
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U coffee"]
      interval: 5s
      retries: 10

volumes:
  pgdata: {}
```

- [ ] **Step 4: `.env.example` 작성 + `.gitignore` 추가 + 실제 `.env` 복사**

`.env.example`:
```
# NVIDIA API 카탈로그 (https://build.nvidia.com)
NVIDIA_API_KEY=
DATABASE_URL=postgresql://coffee:coffee@localhost:5432/coffee
# Kaggle 키는 ~/.kaggle/kaggle.json 에 둔다
```

`.gitignore` 끝에 추가:
```
# coffee pipeline outputs
data/raw/
data/normalized/
data/enriched/
data/embedded/
data/reports/
!data/eval/*.csv
```

실제 키 복사(값을 출력하지 말 것):
```bash
cp /c/githome/wine-sommelier_rag/.env ./.env
```

- [ ] **Step 5: 실패하는 테스트 작성** — `tests/test_settings.py`

```python
from pipeline import settings


def test_paths_are_under_repo_root():
    assert (settings.ROOT / "pyproject.toml").exists()
    assert settings.RAW_DIR == settings.DATA_DIR / "raw"
    assert settings.CURATED_DIR == settings.DATA_DIR / "curated"


def test_database_url_default_is_postgres():
    assert settings.DATABASE_URL.startswith("postgresql://")


def test_load_config_reads_yaml(tmp_path, monkeypatch):
    (tmp_path / "x.yaml").write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    assert settings.load_config("x.yaml") == {"a": 1}
```

- [ ] **Step 6: 실패 확인**

Run: `uv sync && uv run pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 7: 구현** — `pipeline/__init__.py`는 빈 파일, `pipeline/settings.py`:

```python
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
ENRICHED_DIR = DATA_DIR / "enriched"
EMBEDDED_DIR = DATA_DIR / "embedded"
REPORTS_DIR = DATA_DIR / "reports"
CURATED_DIR = DATA_DIR / "curated"
EVAL_DIR = DATA_DIR / "eval"
CONFIG_DIR = ROOT / "config"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://coffee:coffee@localhost:5432/coffee")


def load_config(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
```

- [ ] **Step 8: 통과 확인**

Run: `uv run pytest tests/test_settings.py -v`
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add -A pyproject.toml uv.lock docker-compose.yml .env.example .gitignore pipeline tests
git commit -m "chore: 와인 v1 제거(태그 wine-v1), 커피 파이프라인 골격"
```

---

### Task 2: 레코드 모델 + 정규화 규칙

**Files:**
- Create: `pipeline/records.py`, `pipeline/rules.py`, `tests/test_rules.py`

**Interfaces:**
- Produces (`pipeline.records`):
  - `CoffeeRecord(key, name, roaster=None, origin_country=None, origin_region=None, process=None, roast_level=None, is_decaf=False, decaf_process=None, acidity=None, body=None, sweetness=None, flavor_tags=[], flavor_summary=None, source, source_url=None, collected_at)`
  - `ReviewRecord(key, coffee_key, text, rating=None, sub_scores={}, source, source_url=None, collected_at)`
  - `BrandRecord(key, name, decaf_available, decaf_surcharge_krw=None, default_bean_coffee_key=None, decaf_bean_coffee_key=None, notes=None, source_url=None, verified_at)`
  - `MenuItemRecord(key, brand_key, name, name_en=None, category=None, is_decaf=False, decaf_option=False, caffeine_mg=None, coffee_key=None, source_url=None, collected_at)`
  - `TaxonomyNode(key, parent_key=None, level, name_en, name_ko=None)`
  - `write_jsonl(path: Path, records: Iterable[BaseModel]) -> int`, `read_jsonl(path: Path, model: type[T]) -> list[T]`
- Produces (`pipeline.rules`): `clean(v) -> str | None`, `num(v) -> float | None`, `opt_int(v) -> int | None`, `join_text(*parts) -> str | None`, `detect_decaf(*texts) -> tuple[bool, str | None]`, `normalize_country(text) -> str | None`, `normalize_process(label) -> str | None`, `process_from_text(text) -> str | None`, `normalize_roast(text) -> str | None`, `to_quintile(values) -> pd.Series[Int64]`
  - process 값: `washed|natural|honey|semi-washed|anaerobic|wet-hulled`
  - roast 값: `light|medium-light|medium|medium-dark|dark`
  - decaf_process 값: `swiss-water|mountain-water|sugarcane-ea|co2|methylene-chloride|unknown`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_rules.py`

```python
import math

import pandas as pd
import pytest

from pipeline.records import CoffeeRecord, read_jsonl, write_jsonl
from pipeline.rules import (
    clean, detect_decaf, join_text, normalize_country, normalize_process,
    normalize_roast, num, process_from_text, to_quintile,
)


def test_clean_and_num():
    assert clean(float("nan")) is None
    assert clean("  ") is None
    assert clean(" a ") == "a"
    assert num("8.5") == 8.5
    assert num("abc") is None
    assert join_text("a", None, " ", "b") == "a\nb"
    assert join_text(None, "") is None


@pytest.mark.parametrize("texts,expected", [
    (("Ethiopia Decaf", "Swiss Water process"), (True, "swiss-water")),
    (("콜롬비아 디카페인", "슈가케인 공법"), (True, "sugarcane-ea")),
    (("Colombia decaf",), (True, "unknown")),
    (("Double Carbonic Maceration / Natural", "CO2"), (False, None)),
    (("Decaf Mexico", "supercritical CO2"), (True, "co2")),
    (("Mountain Water Process Mexico",), (True, "mountain-water")),
    (("Kenya AA",), (False, None)),
])
def test_detect_decaf(texts, expected):
    assert detect_decaf(*texts) == expected


@pytest.mark.parametrize("text,expected", [
    ("Nyeri growing region, south-central Kenya", "Kenya"),
    ("United States (Hawaii)", "United States"),
    ("Tanzania, United Republic Of", "Tanzania"),
    ("Sumatra, Indonesia", "Indonesia"),
    ("Ethiopia; Colombia", "Ethiopia"),
    ("에티오피아 예가체프", "Ethiopia"),
    ("Cote d?Ivoire", "Côte d'Ivoire"),
    ("somewhere", None),
    (None, None),
])
def test_normalize_country(text, expected):
    assert normalize_country(text) == expected


@pytest.mark.parametrize("label,expected", [
    ("Washed / Wet", "washed"),
    ("Natural / Dry", "natural"),
    ("Semi-washed / Semi-pulped", "semi-washed"),
    ("Pulped natural / honey", "honey"),
    ("Double Anaerobic Washed", "anaerobic"),
    ("SEMI-LAVADO", "semi-washed"),
    ("Other", None),
    (None, None),
])
def test_normalize_process(label, expected):
    assert normalize_process(label) == expected


@pytest.mark.parametrize("text,expected", [
    ("Produced by smallholders and wet-processed (washed).", "washed"),
    ("dry-processed (natural method)", "natural"),
    ("A natural sweetness with washed clarity", "washed"),
    ("르완다 냐마셰케 내추럴", "natural"),
    ("honey notes and cocoa", None),
])
def test_process_from_text(text, expected):
    assert process_from_text(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Medium-Light", "medium-light"),
    ("Very Dark", "dark"),
    ("Medium", "medium"),
    ("Unknown", None),
    ("블론드 로스트", "light"),
])
def test_normalize_roast(text, expected):
    assert normalize_roast(text) == expected


def test_to_quintile_even_spread_and_missing():
    q = to_quintile(pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, None]))
    assert list(q[:10]) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    assert pd.isna(q.iloc[10])


def test_jsonl_roundtrip(tmp_path):
    rec = CoffeeRecord(key="k", name="n", source="s", collected_at="2026-09-24", flavor_tags=["lemon"])
    p = tmp_path / "x.jsonl"
    assert write_jsonl(p, [rec]) == 1
    assert read_jsonl(p, CoffeeRecord) == [rec]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.records'`

- [ ] **Step 3: `pipeline/records.py` 구현**

```python
import json
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class CoffeeRecord(BaseModel):
    key: str
    name: str
    roaster: str | None = None
    origin_country: str | None = None
    origin_region: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool = False
    decaf_process: str | None = None
    acidity: int | None = Field(default=None, ge=1, le=5)
    body: int | None = Field(default=None, ge=1, le=5)
    sweetness: int | None = Field(default=None, ge=1, le=5)
    flavor_tags: list[str] = Field(default_factory=list)
    flavor_summary: str | None = None
    source: str
    source_url: str | None = None
    collected_at: str


class ReviewRecord(BaseModel):
    key: str
    coffee_key: str
    text: str
    rating: float | None = None
    sub_scores: dict[str, float] = Field(default_factory=dict)
    source: str
    source_url: str | None = None
    collected_at: str


class BrandRecord(BaseModel):
    key: str
    name: str
    decaf_available: bool
    decaf_surcharge_krw: int | None = None
    default_bean_coffee_key: str | None = None
    decaf_bean_coffee_key: str | None = None
    notes: str | None = None
    source_url: str | None = None
    verified_at: str


class MenuItemRecord(BaseModel):
    key: str
    brand_key: str
    name: str
    name_en: str | None = None
    category: str | None = None
    is_decaf: bool = False
    decaf_option: bool = False
    caffeine_mg: float | None = None
    coffee_key: str | None = None
    source_url: str | None = None
    collected_at: str


class TaxonomyNode(BaseModel):
    key: str
    parent_key: str | None = None
    level: int
    name_en: str
    name_ko: str | None = None


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")
            n += 1
    return n


def read_jsonl(path: Path, model: type[T]) -> list[T]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [model.model_validate(json.loads(line)) for line in f if line.strip()]
```

- [ ] **Step 4: `pipeline/rules.py` 구현**

```python
import re

import numpy as np
import pandas as pd


def clean(v) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def num(v) -> float | None:
    s = clean(v)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def opt_int(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


def join_text(*parts) -> str | None:
    kept = [p for p in (clean(x) for x in parts) if p]
    return "\n".join(kept) or None


# --- decaf -------------------------------------------------------------
DECAF_WORD = re.compile(r"decaf|decaffeinat|디카페인", re.I)
# (name, pattern, counts-without-the-word-decaf)
DECAF_PROCESSES = [
    ("swiss-water", re.compile(r"swiss\s*water", re.I), True),
    ("mountain-water", re.compile(r"mountain\s*water", re.I), True),
    ("sugarcane-ea", re.compile(r"sugar\s*cane|ethyl\s*acetate|\bE\.?A\.?\s+process|슈가\s*케인|사탕수수", re.I), False),
    ("co2", re.compile(r"\bco2\b|carbon dioxide|supercritical", re.I), False),
    ("methylene-chloride", re.compile(r"methylene chloride|european process", re.I), False),
]


def detect_decaf(*texts) -> tuple[bool, str | None]:
    t = " ".join(x for x in (clean(v) for v in texts) if x)
    has_word = DECAF_WORD.search(t) is not None
    for name, pat, standalone in DECAF_PROCESSES:
        if pat.search(t) and (standalone or has_word):
            return True, name
    return (True, "unknown") if has_word else (False, None)


# --- country -------------------------------------------------------------
_COUNTRIES = [
    "Ethiopia", "Kenya", "Colombia", "Brazil", "Guatemala", "Costa Rica", "Panama", "Honduras",
    "El Salvador", "Nicaragua", "Mexico", "Peru", "Bolivia", "Ecuador", "Rwanda", "Burundi",
    "Uganda", "Tanzania", "Democratic Republic of the Congo", "Malawi", "Zambia", "Zimbabwe",
    "Yemen", "India", "Indonesia", "Papua New Guinea", "Vietnam", "Thailand", "Laos", "Myanmar",
    "China", "Taiwan", "Philippines", "Jamaica", "Haiti", "Dominican Republic", "Puerto Rico",
    "Cuba", "Venezuela", "United States", "Mauritius", "Madagascar", "Cameroon", "Timor-Leste",
    "Nepal", "Australia",
]
_ALIASES = {
    "hawaii": "United States", "kona": "United States", "usa": "United States",
    "sumatra": "Indonesia", "java": "Indonesia", "sulawesi": "Indonesia", "bali": "Indonesia",
    "congo": "Democratic Republic of the Congo", "east timor": "Timor-Leste",
    "cote d'ivoire": "Côte d'Ivoire", "cote d?ivoire": "Côte d'Ivoire", "côte d'ivoire": "Côte d'Ivoire",
    "yirgacheffe": "Ethiopia", "sidama": "Ethiopia", "guji": "Ethiopia", "harrar": "Ethiopia",
}
_KO = {
    "에티오피아": "Ethiopia", "케냐": "Kenya", "콜롬비아": "Colombia", "브라질": "Brazil",
    "과테말라": "Guatemala", "코스타리카": "Costa Rica", "파나마": "Panama", "온두라스": "Honduras",
    "엘살바도르": "El Salvador", "니카라과": "Nicaragua", "멕시코": "Mexico", "페루": "Peru",
    "볼리비아": "Bolivia", "에콰도르": "Ecuador", "르완다": "Rwanda", "부룬디": "Burundi",
    "우간다": "Uganda", "탄자니아": "Tanzania", "예멘": "Yemen", "인도네시아": "Indonesia",
    "파푸아뉴기니": "Papua New Guinea", "베트남": "Vietnam", "중국": "China", "하와이": "United States",
    "예가체프": "Ethiopia", "시다마": "Ethiopia", "구지": "Ethiopia",
}
_COUNTRY_PATTERNS = [(re.compile(rf"(?<![a-z]){re.escape(c.lower())}(?![a-z])"), c) for c in _COUNTRIES]
_COUNTRY_PATTERNS += [(re.compile(rf"(?<![a-z]){re.escape(a)}(?![a-z])"), c) for a, c in _ALIASES.items()]
_COUNTRY_PATTERNS += [(re.compile(re.escape(k)), c) for k, c in _KO.items()]


def normalize_country(text) -> str | None:
    t = clean(text)
    if not t:
        return None
    t = t.lower()
    best: tuple[int, str] | None = None
    for pat, canon in _COUNTRY_PATTERNS:
        m = pat.search(t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), canon)
    return best[1] if best else None


# --- process -------------------------------------------------------------
_LABEL_PROCESS = [
    ("anaerobic", re.compile(r"anaerob|carbonic|maceration|무산소|애너로빅", re.I)),
    ("wet-hulled", re.compile(r"wet[\s-]?hull|giling", re.I)),
    ("honey", re.compile(r"honey|pulped natural|miel|허니", re.I)),
    ("semi-washed", re.compile(r"semi[\s-]?(washed|lavado|pulped)", re.I)),
    ("natural", re.compile(r"natural|\bdry\b|내추럴", re.I)),
    ("washed", re.compile(r"washed|\bwet\b|lavado|워시드", re.I)),
]
_TEXT_PROCESS = [
    ("anaerobic", re.compile(r"anaerobic|carbonic maceration|무산소|애너로빅", re.I)),
    ("wet-hulled", re.compile(r"wet[\s-]hull", re.I)),
    ("honey", re.compile(r"honey[\s-]process|pulped[\s-]natural|허니\s*프로세스", re.I)),
    ("natural", re.compile(r"dry[\s-]process|natural[\s-]process|\(natural|natural method|내추럴", re.I)),
    ("washed", re.compile(r"wet[\s-]process|washed[\s-]process|fully washed|\(washed\)|\bwashed\b|워시드", re.I)),
]


def normalize_process(label) -> str | None:
    t = clean(label)
    if not t:
        return None
    for name, pat in _LABEL_PROCESS:
        if pat.search(t):
            return name
    return None


def process_from_text(text) -> str | None:
    t = clean(text)
    if not t:
        return None
    for name, pat in _TEXT_PROCESS:
        if pat.search(t):
            return name
    return None


# --- roast ---------------------------------------------------------------
_ROAST = [
    ("medium-light", re.compile(r"medium[\s-]*light|light[\s-]*medium|미디엄\s*라이트", re.I)),
    ("medium-dark", re.compile(r"medium[\s-]*dark|미디엄\s*다크", re.I)),
    ("light", re.compile(r"\blight\b|라이트|약배전|blonde|블론드", re.I)),
    ("dark", re.compile(r"dark|다크|강배전|french|italian", re.I)),
    ("medium", re.compile(r"medium|미디엄|중배전", re.I)),
]


def normalize_roast(text) -> str | None:
    t = clean(text)
    if not t:
        return None
    for name, pat in _ROAST:
        if pat.search(t):
            return name
    return None


# --- scores --------------------------------------------------------------
def to_quintile(values) -> pd.Series:
    """Map numeric scores to 1-5 by within-source percentile rank. Missing stays <NA>."""
    s = pd.to_numeric(pd.Series(values), errors="coerce")
    ranks = s.rank(pct=True, method="average")
    q = np.ceil(ranks * 5 - 1e-9).clip(1, 5)
    return q.astype("Int64")
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_rules.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add pipeline/records.py pipeline/rules.py tests/test_rules.py
git commit -m "feat(pipeline): 레코드 모델과 정규화 규칙(디카페인·산지·가공·로스팅·5분위)"
```

---

### Task 3: PoliteClient + 수집 프레임워크

**Files:**
- Create: `pipeline/http.py`, `pipeline/collect/__init__.py`, `tests/test_http.py`, `tests/test_collect.py`

**Interfaces:**
- Produces (`pipeline.http`): `UA: str`, `RobotsDisallowed(Exception)`, `PoliteClient(delay=1.0, user_agent=UA, transport=None, sleep=time.sleep, timeout=30.0)` with `.allowed(url) -> bool`, `.get(url, params=None) -> httpx.Response` (raises `RobotsDisallowed`, `httpx.HTTPStatusError`), `.download(url, dest: Path) -> Path`
- Produces (`pipeline.collect`): `Manifest(source: str, snapshot: str, files: list[str], ok: bool, error: str | None = None)`, `Collector` Protocol (`name: str`, `collect(out_dir: Path, http: PoliteClient) -> list[Path]`), `run_collect(collectors, raw_root: Path, http, snapshot: str) -> list[Manifest]`, `latest_snapshot(raw_root: Path, source: str) -> Path | None`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_http.py`

```python
import httpx
import pytest

from pipeline.http import PoliteClient, RobotsDisallowed


def make_client(routes, sleeps):
    def handler(request: httpx.Request):
        fn = routes.get(request.url.path)
        return fn(request) if fn else httpx.Response(404)
    return PoliteClient(delay=1.0, transport=httpx.MockTransport(handler), sleep=sleeps.append)


def test_disallowed_by_robots_raises():
    c = make_client({"/robots.txt": lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /private\n"),
                     "/private/x": lambda r: httpx.Response(200, text="secret")}, [])
    with pytest.raises(RobotsDisallowed):
        c.get("https://a.test/private/x")


def test_missing_robots_allows_and_delays_second_request():
    sleeps = []
    c = make_client({"/data": lambda r: httpx.Response(200, text="ok")}, sleeps)
    assert c.get("https://a.test/data").text == "ok"
    assert c.get("https://a.test/data").text == "ok"
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0


def test_download_writes_file(tmp_path):
    c = make_client({"/f.csv": lambda r: httpx.Response(200, content=b"a,b\n1,2\n")}, [])
    p = c.download("https://a.test/f.csv", tmp_path / "sub" / "f.csv")
    assert p.read_bytes() == b"a,b\n1,2\n"
```

`tests/test_collect.py`:
```python
import json
from dataclasses import dataclass

from pipeline.collect import latest_snapshot, run_collect


@dataclass
class OkCollector:
    name: str = "ok_src"

    def collect(self, out_dir, http):
        p = out_dir / "a.txt"
        p.write_text("x", encoding="utf-8")
        return [p]


@dataclass
class BoomCollector:
    name: str = "boom_src"

    def collect(self, out_dir, http):
        raise RuntimeError("site down")


def test_run_collect_isolates_failures(tmp_path):
    ms = run_collect([BoomCollector(), OkCollector()], tmp_path, http=None, snapshot="2026-09-24")
    assert [(m.source, m.ok) for m in ms] == [("boom_src", False), ("ok_src", True)]
    assert "site down" in ms[0].error
    manifest = json.loads((tmp_path / "ok_src" / "2026-09-24" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == ["a.txt"]


def test_latest_snapshot_skips_failed(tmp_path):
    run_collect([OkCollector()], tmp_path, None, "2026-09-01")
    run_collect([OkCollector()], tmp_path, None, "2026-09-10")
    (tmp_path / "ok_src" / "2026-09-20").mkdir()
    (tmp_path / "ok_src" / "2026-09-20" / "manifest.json").write_text('{"ok": false}', encoding="utf-8")
    assert latest_snapshot(tmp_path, "ok_src").name == "2026-09-10"
    assert latest_snapshot(tmp_path, "none") is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_http.py tests/test_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.http'`

- [ ] **Step 3: `pipeline/http.py` 구현**

```python
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

UA = "CoffeeSommelierBot/0.1 (+portfolio project)"


class RobotsDisallowed(Exception):
    pass


class PoliteClient:
    """HTTP GET with robots.txt checks and a per-host delay."""

    def __init__(self, delay: float = 1.0, user_agent: str = UA, transport=None, sleep=time.sleep, timeout: float = 30.0):
        self._client = httpx.Client(headers={"User-Agent": user_agent}, transport=transport,
                                    timeout=timeout, follow_redirects=True)
        self._delay = delay
        self._sleep = sleep
        self._ua = user_agent
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    def _robots_for(self, url: str):
        p = urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                r = self._client.get(host + "/robots.txt")
                if r.status_code >= 400:
                    rp = None
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[host] = rp
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(self._ua, url)

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        if host in self._last:
            wait = self._delay - (time.monotonic() - self._last[host])
            if wait > 0:
                self._sleep(wait)
        r = self._client.get(url, params=params)
        self._last[host] = time.monotonic()
        r.raise_for_status()
        return r

    def download(self, url: str, dest: Path) -> Path:
        r = self.get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest
```

- [ ] **Step 4: `pipeline/collect/__init__.py` 구현**

```python
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Manifest:
    source: str
    snapshot: str
    files: list[str]
    ok: bool
    error: str | None = None


class Collector(Protocol):
    name: str

    def collect(self, out_dir: Path, http) -> list[Path]: ...


def run_collect(collectors, raw_root: Path, http, snapshot: str) -> list[Manifest]:
    results = []
    for c in collectors:
        out = raw_root / c.name / snapshot
        out.mkdir(parents=True, exist_ok=True)
        try:
            files = c.collect(out, http)
            m = Manifest(c.name, snapshot, [f.relative_to(out).as_posix() for f in files], True)
        except Exception as e:  # one broken source must not stop the others
            m = Manifest(c.name, snapshot, [], False, f"{type(e).__name__}: {e}")
        (out / "manifest.json").write_text(json.dumps(asdict(m), ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(m)
    return results


def latest_snapshot(raw_root: Path, source: str) -> Path | None:
    base = raw_root / source
    if not base.exists():
        return None
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        mf = d / "manifest.json"
        if mf.exists() and json.loads(mf.read_text(encoding="utf-8")).get("ok"):
            return d
    return None
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_http.py tests/test_collect.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add pipeline/http.py pipeline/collect tests/test_http.py tests/test_collect.py
git commit -m "feat(pipeline): robots 준수 HTTP 클라이언트와 소스 격리 수집 프레임워크"
```

---

### Task 4: 데이터셋 수집기 (CQI, RoasterDB, SCA, Kaggle)

**Files:**
- Create: `pipeline/collect/datasets.py`
- Test: `tests/test_collect.py` (추가)

**Interfaces:**
- Consumes: `PoliteClient.download`
- Produces: `UrlFilesCollector(name: str, urls: dict[str, str])`, 인스턴스 `CQI` (name `"cqi"`, 파일 `arabica_2018.csv`, `robusta_2018.csv`, `arabica_2023.csv`), `ROASTERDB` (name `"roasterdb"`, 파일 `roasterdb_sample.csv`), `SCA` (name `"sca_wheel"`, 파일 `sca_coffee_flavors.json`), `KaggleCollector(name="coffeereview_kaggle", datasets=(...), downloader=None)` — 데이터셋마다 `out_dir/<owner>__<slug>/*.csv`

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_collect.py` 끝에

```python
import httpx

from pipeline.collect.datasets import CQI, KaggleCollector, UrlFilesCollector
from pipeline.http import PoliteClient


def test_url_files_collector_downloads_each_file(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, content=b"x") if request.url.path != "/robots.txt" else httpx.Response(404)

    http = PoliteClient(delay=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    c = UrlFilesCollector("t", {"a.csv": "https://h.test/a.csv", "b.json": "https://h.test/b.json"})
    files = c.collect(tmp_path, http)
    assert sorted(f.name for f in files) == ["a.csv", "b.json"]
    assert "/a.csv" in seen and "/b.json" in seen


def test_cqi_collector_targets_three_files():
    assert set(CQI.urls) == {"arabica_2018.csv", "robusta_2018.csv", "arabica_2023.csv"}


def test_kaggle_collector_uses_downloader(tmp_path):
    def fake_dl(dataset, dest):
        (dest / "data.csv").write_text("a\n1\n", encoding="utf-8")

    c = KaggleCollector(datasets=("own/one", "own/two"), downloader=fake_dl)
    files = c.collect(tmp_path, http=None)
    assert [f.relative_to(tmp_path).as_posix() for f in files] == ["own__one/data.csv", "own__two/data.csv"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.collect.datasets'`

- [ ] **Step 3: 구현** — `pipeline/collect/datasets.py`

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

GH = "https://raw.githubusercontent.com"


@dataclass
class UrlFilesCollector:
    name: str
    urls: dict[str, str]

    def collect(self, out_dir: Path, http) -> list[Path]:
        return [http.download(url, out_dir / filename) for filename, url in self.urls.items()]


CQI = UrlFilesCollector("cqi", {
    "arabica_2018.csv": f"{GH}/jldbc/coffee-quality-database/master/data/arabica_data_cleaned.csv",
    "robusta_2018.csv": f"{GH}/jldbc/coffee-quality-database/master/data/robusta_data_cleaned.csv",
    "arabica_2023.csv": f"{GH}/fatih-boyar/coffee-quality-data-CQI/main/df_arabica_clean.csv",
})
ROASTERDB = UrlFilesCollector("roasterdb", {
    "roasterdb_sample.csv": f"{GH}/RoasterDB/specialty-coffee-roasterdb/main/samples/roasterdb_sample.csv",
})
SCA = UrlFilesCollector("sca_wheel", {
    "sca_coffee_flavors.json": f"{GH}/fschlz/coffee-flavor-api/master/resources/sca_coffee_flavors.json",
})


def _kaggle_download(dataset: str, dest: Path) -> None:
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()  # reads ~/.kaggle/kaggle.json
    api.dataset_download_files(dataset, path=str(dest), unzip=True, quiet=True)


@dataclass
class KaggleCollector:
    name: str = "coffeereview_kaggle"
    datasets: tuple[str, ...] = (
        "patkle/coffeereviewcom-over-7000-ratings-and-reviews",
        "hanifalirsyad/coffee-scrap-coffeereview",
        "schmoyote/coffee-reviews-dataset",
    )
    downloader: Callable[[str, Path], None] | None = field(default=None, repr=False)

    def collect(self, out_dir: Path, http) -> list[Path]:
        dl = self.downloader or _kaggle_download
        files: list[Path] = []
        for ds in self.datasets:
            d = out_dir / ds.replace("/", "__")
            d.mkdir(parents=True, exist_ok=True)
            dl(ds, d)
            files += sorted(d.glob("*.csv"))
        return files
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_collect.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/collect/datasets.py tests/test_collect.py
git commit -m "feat(collect): CQI·RoasterDB·SCA·Kaggle 데이터셋 수집기"
```

---

### Task 5: 웹 수집기 (스타벅스, 메가, 빽다방, Shopify) + 레지스트리

**Files:**
- Create: `pipeline/collect/web.py`, `pipeline/collect/registry.py`, `config/sources.yaml`
- Test: `tests/test_collect.py` (추가)

**Interfaces:**
- Consumes: `PoliteClient.get`, `settings.load_config`
- Produces:
  - `StarbucksCollector(name="starbucks", codes=("W0000003", "W0000004"))` → `out_dir/<code>.json` (원문 JSON)
  - `MegaCollector(name="mega", max_pages=30)` → `out_dir/page_<n>.html`, 항목 0개인 페이지에서 중단
  - `PaikCollector(name="paik")` → `out_dir/coffee.html`
  - `ShopifyCollector(name="shopify", domains=None)` → `out_dir/<domain>.json` = `{"products": [...]}`; `domains=None`이면 `config/sources.yaml`의 `shopify[].domain`
  - `pipeline.collect.registry.ALL_COLLECTORS: list` (CQI, ROASTERDB, SCA, KaggleCollector(), StarbucksCollector(), MegaCollector(), PaikCollector(), ShopifyCollector())

- [ ] **Step 1: `config/sources.yaml` 작성**

```yaml
# Shopify 스토어 원두 수집 대상. 추가 전 https://<domain>/robots.txt 에서 /products.json 허용 여부 확인.
shopify:
  - domain: kr.bluebottlecoffee.com
    roaster: Blue Bottle Coffee
    product_types: ["원두"]
```

- [ ] **Step 2: 실패하는 테스트 추가** — `tests/test_collect.py` 끝에

```python
import json as _json

from pipeline.collect.web import MegaCollector, PaikCollector, ShopifyCollector, StarbucksCollector


def mock_http(handler):
    def wrapped(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return handler(request)
    return PoliteClient(delay=0, transport=httpx.MockTransport(wrapped), sleep=lambda s: None)


def test_starbucks_skips_non_json_codes(tmp_path):
    def handler(request):
        if request.url.path.endswith("W0000003.js"):
            return httpx.Response(200, text='{"list": [{"product_NM": "아메리카노"}]}')
        return httpx.Response(200, text="<html>not json</html>")

    files = StarbucksCollector(codes=("W0000003", "W0000001")).collect(tmp_path, mock_http(handler))
    assert [f.name for f in files] == ["W0000003.json"]


def test_mega_stops_at_empty_page(tmp_path):
    def handler(request):
        page = int(request.url.params["page"])
        body = '<li><a class="inner_modal_open"></a></li>' if page <= 2 else "<ul></ul>"
        return httpx.Response(200, text=body)

    files = MegaCollector().collect(tmp_path, mock_http(handler))
    assert [f.name for f in files] == ["page_1.html", "page_2.html"]


def test_paik_saves_page(tmp_path):
    files = PaikCollector().collect(tmp_path, mock_http(lambda r: httpx.Response(200, text="<div class='hover'></div>")))
    assert files[0].read_text(encoding="utf-8") == "<div class='hover'></div>"


def test_shopify_paginates(tmp_path):
    def handler(request):
        page = int(request.url.params["page"])
        products = [{"id": page, "title": f"p{page}"}] if page <= 2 else []
        return httpx.Response(200, json={"products": products})

    files = ShopifyCollector(domains=("shop.test",)).collect(tmp_path, mock_http(handler))
    data = _json.loads(files[0].read_text(encoding="utf-8"))
    assert files[0].name == "shop.test.json"
    assert [p["id"] for p in data["products"]] == [1, 2]


def test_registry_lists_all_sources():
    from pipeline.collect.registry import ALL_COLLECTORS
    assert [c.name for c in ALL_COLLECTORS] == [
        "cqi", "roasterdb", "sca_wheel", "coffeereview_kaggle", "starbucks", "mega", "paik", "shopify"]
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.collect.web'`

- [ ] **Step 4: 구현** — `pipeline/collect/web.py`

```python
import json
from dataclasses import dataclass
from pathlib import Path

from pipeline import settings

STARBUCKS_URL = "https://www.starbucks.co.kr/upload/json/menu/{code}.js"
MEGA_URL = "https://www.mega-mgccoffee.com/menu/menu.php"
PAIK_URL = "https://paikdabang.com/menu/menu_coffee/"


@dataclass
class StarbucksCollector:
    name: str = "starbucks"
    codes: tuple[str, ...] = ("W0000003", "W0000004")  # 에스프레소(아메리카노 등), 블렌디드 커피

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for code in self.codes:
            text = http.get(STARBUCKS_URL.format(code=code)).text
            try:
                json.loads(text)
            except ValueError:
                continue  # unknown category code returns an HTML page
            p = out_dir / f"{code}.json"
            p.write_text(text, encoding="utf-8")
            files.append(p)
        return files


@dataclass
class MegaCollector:
    name: str = "mega"
    max_pages: int = 30

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for page in range(1, self.max_pages + 1):
            params = {"menu_category1": 1, "menu_category2": 1, "category": "",
                      "list_checkbox_all": "all", "page": page}
            html = http.get(MEGA_URL, params=params).text
            if "inner_modal_open" not in html:
                break
            p = out_dir / f"page_{page}.html"
            p.write_text(html, encoding="utf-8")
            files.append(p)
        return files


@dataclass
class PaikCollector:
    name: str = "paik"

    def collect(self, out_dir: Path, http) -> list[Path]:
        p = out_dir / "coffee.html"
        p.write_text(http.get(PAIK_URL).text, encoding="utf-8")
        return [p]


@dataclass
class ShopifyCollector:
    name: str = "shopify"
    domains: tuple[str, ...] | None = None
    max_pages: int = 40

    def collect(self, out_dir: Path, http) -> list[Path]:
        domains = self.domains or tuple(s["domain"] for s in settings.load_config("sources.yaml")["shopify"])
        files = []
        for domain in domains:
            products = []
            for page in range(1, self.max_pages + 1):
                batch = http.get(f"https://{domain}/products.json", params={"limit": 250, "page": page}).json().get("products", [])
                if not batch:
                    break
                products += batch
            p = out_dir / f"{domain}.json"
            p.write_text(json.dumps({"products": products}, ensure_ascii=False), encoding="utf-8")
            files.append(p)
        return files
```

`pipeline/collect/registry.py`:
```python
from pipeline.collect.datasets import CQI, ROASTERDB, SCA, KaggleCollector
from pipeline.collect.web import MegaCollector, PaikCollector, ShopifyCollector, StarbucksCollector

ALL_COLLECTORS = [
    CQI, ROASTERDB, SCA, KaggleCollector(),
    StarbucksCollector(), MegaCollector(), PaikCollector(), ShopifyCollector(),
]
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_collect.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add pipeline/collect config/sources.yaml tests/test_collect.py
git commit -m "feat(collect): 스타벅스·메가·빽다방·Shopify 수집기와 레지스트리"
```

---

### Task 6: 데이터셋 정규화 (coffeereview, CQI, RoasterDB, SCA)

**Files:**
- Create: `pipeline/normalize/__init__.py` (Normalized만 — run_normalize는 Task 7), `pipeline/normalize/datasets.py`, `data/curated/sca_ko.yaml`, `tests/test_normalize_datasets.py`

**Interfaces:**
- Consumes: `pipeline.records.*`, `pipeline.rules.*`
- Produces:
  - `pipeline.normalize.Normalized` dataclass: `coffees, reviews, brands, menu_items, taxonomy` (각 list), `.extend(other)`, `.size() -> int`
  - `normalize_coffeereview(snap: Path, collected_at: str) -> Normalized` — 키 `coffeereview:<url>` 또는 `coffeereview:<roaster>|<name>`(소문자), 리뷰 키 `review:<coffee_key>`, `source="coffeereview_kaggle"`
  - `normalize_cqi(snap, collected_at) -> Normalized` — 키 `cqi:<file_stem>:<row>`, 리뷰 없음, `source="cqi"`
  - `normalize_roasterdb(snap, collected_at) -> Normalized` — 키 `roasterdb:<product_id>`, `source="roasterdb"`
  - `normalize_sca(snap, collected_at, ko_path: Path | None = None) -> Normalized` — 키 `sca:<path>` (경로 구분자 `>`), level 1~3
  - `parse_sca_nodes(s: str) -> list[str]`

- [ ] **Step 1: `data/curated/sca_ko.yaml` 작성** (SCA 휠 1·2단계 한국어. 원본 JSON은 수정하지 않는다)

```yaml
fruity: 과일
fruity>berry: 베리
fruity>dried fruit: 말린 과일
fruity>other fruit: 기타 과일
fruity>citrus fruit: 시트러스
sour/fermented: 신맛/발효
sour/fermented>sour: 신맛
sour/fermented>alcohol fermented: 알코올/발효
green/vegetative: 풀/채소
green/vegetative>olive oil: 올리브유
green/vegetative>raw: 날것
green/vegetative>green vegetative: 풋내
green/vegetative>beany: 콩비린내
other: 기타
other>chemical: 화학적
other>papery/musty: 종이/곰팡내
roasted: 로스팅
roasted>cereal: 곡물
roasted>burnt: 탄맛
roasted>tobacco: 담배
roasted>pipe tobacco: 파이프 담배
spices: 향신료
spices>brown spice: 브라운 스파이스
spices>pepper: 후추
spices>pungent: 자극적
nutty/cocoa: 견과/코코아
nutty/cocoa>cocoa: 코코아
nutty/cocoa>nutty: 견과
sweet: 단맛
sweet>brown sugar: 흑설탕
sweet>vanilla: 바닐라
sweet>vanillin: 바닐린
sweet>overall sweet: 전반적 단맛
sweet>sweet aromatics: 달콤한 향
floral: 꽃
floral>black tea: 홍차
floral>floral: 꽃향
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_normalize_datasets.py`

```python
import json

from pipeline.normalize.datasets import (
    normalize_coffeereview, normalize_cqi, normalize_roasterdb, normalize_sca, parse_sca_nodes,
)

PATKLE = (
    "title,rating,acidity_structure,aftertaste,aroma,body,flavor,with_milk,agtron,blind_assessment,"
    "bottom_line,coffee_origin,est_price,notes,review_date,roast_level,roaster,roaster_location,url\n"
    'Bolivia Gesha,93,9,8,9,8,9,,60/78,"Floral. Magnolia, cocoa nib.","Great.","Caranavi, Bolivia",$30,'
    '"Washed process.",January 2023,Medium-Light,Red Rooster,Floyd,https://cr.test/review/a/\n'
    'Decaf Colombia,90,7,8,8,9,8,,55/70,"Cocoa, walnut.","Good decaf.","Huila, Colombia",$18,'
    '"Swiss Water decaffeinated.",March 2022,Medium,Roaster B,Here,https://cr.test/review/b/\n'
)
HANIF = (
    "slug,all_text,rating,roaster,name,location,origin,roast,est_price,review_date,agtron,"
    "aroma,acid,body,flavor,aftertaste,with_milk,desc_1,desc_2,desc_3\n"
    "https://cr.test/review/a/,x,93,Red Rooster,Bolivia Gesha,Floyd,\"Caranavi, Bolivia\",Medium-Light,$30,"
    "Jan 2023,60/78,9,9,8,9,8,,d1,d2,d3\n"
    "https://cr.test/review/c/,x,91,Lu's,Kenya AA,Taipei,\"Nyeri, Kenya\",Light,NT,Oct 2023,58/78,"
    "9,8,7,9,8,,\"Black currant, lemon.\",Washed.,Bright.\n"
)
SCHMOYOTE = (
    "name,roaster,roast,loc_country,origin_1,origin_2,100g_USD,rating,review_date,desc_1,desc_2,desc_3\n"
    "Kenya AA,Lu's,Light,Taiwan,Kenya,,5,91,2023,dup,dup,dup\n"
    "Sweety Blend,A.R.C.,Medium-Light,Hong Kong,Panama,Ethiopia,14,95,2017,\"Chocolaty, vanilla.\",Blend.,Rich.\n"
)


def write_cr(tmp_path):
    for sub, fn, body in [("patkle__x", "reviews_feb_2023.csv", PATKLE),
                          ("hanif__y", "coffee_clean.csv", HANIF),
                          ("schmoyote__z", "coffee_analysis.csv", SCHMOYOTE)]:
        (tmp_path / sub).mkdir()
        (tmp_path / sub / fn).write_text(body, encoding="utf-8")


def test_coffeereview_merges_three_sources(tmp_path):
    write_cr(tmp_path)
    n = normalize_coffeereview(tmp_path, "2026-09-24")
    keys = sorted(c.key for c in n.coffees)
    assert keys == ["coffeereview:a.r.c.|sweety blend", "coffeereview:https://cr.test/review/a/",
                    "coffeereview:https://cr.test/review/b/", "coffeereview:https://cr.test/review/c/"]
    by = {c.key: c for c in n.coffees}
    a = by["coffeereview:https://cr.test/review/a/"]
    assert (a.origin_country, a.process, a.roast_level, a.is_decaf) == ("Bolivia", "washed", "medium-light", False)
    b = by["coffeereview:https://cr.test/review/b/"]
    assert (b.is_decaf, b.decaf_process) == (True, "swiss-water")
    assert all(c.acidity is None or 1 <= c.acidity <= 5 for c in n.coffees)
    assert by["coffeereview:a.r.c.|sweety blend"].origin_country == "Panama"
    assert len(n.reviews) == 4
    assert all(r.coffee_key in by for r in n.reviews)


CQI18 = (
    "Unnamed: 0,Species,Owner,Country.of.Origin,Farm.Name,Company,Region,Variety,Processing.Method,"
    "Acidity,Body,Sweetness\n"
    "1,Arabica,metad,Ethiopia,metad plc,metad co,guji,,Washed / Wet,8.75,8.5,10\n"
    "2,Arabica,x,Mexico,finca,Descafeinadores Mexicano,chiapas,,Natural / Dry,7.0,7.2,10\n"
)
CQI23 = (
    "Unnamed: 0,ID,Country of Origin,Farm Name,Company,Region,Variety,Processing Method,Acidity,Body,Sweetness,Owner\n"
    "0,0,Colombia,Finca El Paraiso,CQU,Cauca,Castillo,Double Anaerobic Washed,8.58,8.25,10,CQU\n"
)


def test_cqi_maps_both_schemas(tmp_path):
    (tmp_path / "arabica_2018.csv").write_text(CQI18, encoding="utf-8")
    (tmp_path / "arabica_2023.csv").write_text(CQI23, encoding="utf-8")
    n = normalize_cqi(tmp_path, "2026-09-24")
    by = {c.key: c for c in n.coffees}
    assert set(by) == {"cqi:arabica_2018:0", "cqi:arabica_2018:1", "cqi:arabica_2023:0"}
    assert by["cqi:arabica_2018:0"].acidity == 5 and by["cqi:arabica_2018:1"].acidity == 2
    assert by["cqi:arabica_2023:0"].process == "anaerobic"
    assert by["cqi:arabica_2018:1"].is_decaf is False  # "Descafeinadores" alone is not the word decaf
    assert all(c.sweetness is None for c in n.coffees)
    assert n.reviews == []


def test_roasterdb(tmp_path):
    (tmp_path / "roasterdb_sample.csv").write_text(
        "product_id,source_roaster,title,origin_country,origin_region,process_method,roast_level,"
        "tasting_notes_sca_nodes,source_url\n"
        "14,3fe,Bolivia Natural,Bolivia,,Natural,Unknown,Sweet > Honey > Honey; Fruity > Berry > Blueberry,https://r.test/p\n",
        encoding="utf-8")
    n = normalize_roasterdb(tmp_path, "2026-09-24")
    c = n.coffees[0]
    assert (c.key, c.process, c.roast_level) == ("roasterdb:14", "natural", None)
    assert c.flavor_tags == ["honey", "blueberry"]


def test_parse_sca_nodes_dedupes():
    assert parse_sca_nodes("A > B > lemon | A > B > Lemon; C > lime") == ["lemon", "lime"]


def test_sca_walks_tree_with_korean(tmp_path):
    (tmp_path / "sca_coffee_flavors.json").write_text(
        json.dumps({"fruity": {"berry": ["blackberry"], "citrus fruit": ["lemon"]}}), encoding="utf-8")
    ko = tmp_path / "ko.yaml"
    ko.write_text("fruity: 과일\nfruity>berry: 베리\n", encoding="utf-8")
    n = normalize_sca(tmp_path, "2026-09-24", ko_path=ko)
    by = {t.key: t for t in n.taxonomy}
    assert set(by) == {"sca:fruity", "sca:fruity>berry", "sca:fruity>berry>blackberry",
                       "sca:fruity>citrus fruit", "sca:fruity>citrus fruit>lemon"}
    assert by["sca:fruity>berry>blackberry"].parent_key == "sca:fruity>berry"
    assert by["sca:fruity>berry>blackberry"].level == 3
    assert by["sca:fruity>berry"].name_ko == "베리"
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_normalize_datasets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.normalize'`

- [ ] **Step 4: `pipeline/normalize/__init__.py` 구현 (Normalized)**

```python
from dataclasses import dataclass, field

from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode


@dataclass
class Normalized:
    coffees: list[CoffeeRecord] = field(default_factory=list)
    reviews: list[ReviewRecord] = field(default_factory=list)
    brands: list[BrandRecord] = field(default_factory=list)
    menu_items: list[MenuItemRecord] = field(default_factory=list)
    taxonomy: list[TaxonomyNode] = field(default_factory=list)

    def extend(self, other: "Normalized") -> None:
        self.coffees += other.coffees
        self.reviews += other.reviews
        self.brands += other.brands
        self.menu_items += other.menu_items
        self.taxonomy += other.taxonomy

    def size(self) -> int:
        return len(self.coffees) + len(self.reviews) + len(self.brands) + len(self.menu_items) + len(self.taxonomy)
```

- [ ] **Step 5: `pipeline/normalize/datasets.py` 구현**

```python
import json
import re
from pathlib import Path

import pandas as pd
import yaml

from pipeline import settings
from pipeline.normalize import Normalized
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode
from pipeline.rules import (
    clean, detect_decaf, join_text, normalize_country, normalize_process, normalize_roast,
    num, opt_int, process_from_text, to_quintile,
)


def _find(snap: Path, filename: str) -> Path | None:
    hits = sorted(snap.rglob(filename))
    return hits[0] if hits else None


def _records(path: Path) -> list[dict]:
    return pd.read_csv(path, dtype=str).to_dict("records")


def _empty(v) -> bool:
    return v is None or v == "" or v == {}


def _fill_missing(base: dict | None, new: dict) -> dict:
    if base is None:
        return new
    for k, v in new.items():
        if _empty(base.get(k)) and not _empty(v):
            base[k] = v
    return base


def _subs(r: dict, keys) -> dict[str, float]:
    return {k: v for k in keys if (v := num(r.get(k))) is not None}


# --- coffeereview (3 Kaggle scrapes of coffeereview.com) ------------------
def normalize_coffeereview(snap: Path, collected_at: str) -> Normalized:
    rows: dict[str, dict] = {}

    if p := _find(snap, "reviews_feb_2023.csv"):
        for r in _records(p):
            url = clean(r.get("url"))
            if not url:
                continue
            rows[url] = {
                "url": url, "name": clean(r.get("title")), "roaster": clean(r.get("roaster")),
                "origin": clean(r.get("coffee_origin")), "roast": clean(r.get("roast_level")),
                "acid": num(r.get("acidity_structure")), "body": num(r.get("body")), "rating": num(r.get("rating")),
                "summary": clean(r.get("blind_assessment")),
                "text": join_text(r.get("blind_assessment"), r.get("notes"), r.get("bottom_line")),
                "subs": _subs(r, ("aroma", "flavor", "aftertaste", "with_milk")),
            }

    if p := _find(snap, "coffee_clean.csv"):
        for r in _records(p):
            url = clean(r.get("slug"))
            if not url:
                continue
            new = {
                "url": url, "name": clean(r.get("name")), "roaster": clean(r.get("roaster")),
                "origin": clean(r.get("origin")), "roast": clean(r.get("roast")),
                "acid": num(r.get("acid")), "body": num(r.get("body")), "rating": num(r.get("rating")),
                "summary": clean(r.get("desc_1")), "text": join_text(r.get("desc_1"), r.get("desc_2"), r.get("desc_3")),
                "subs": _subs(r, ("aroma", "flavor", "aftertaste", "with_milk")),
            }
            rows[url] = _fill_missing(rows.get(url), new)

    if p := _find(snap, "coffee_analysis.csv"):
        index = {((v["roaster"] or "").lower(), (v["name"] or "").lower()): k for k, v in rows.items()}
        for r in _records(p):
            ident = ((clean(r.get("roaster")) or "").lower(), (clean(r.get("name")) or "").lower())
            new = {
                "url": None, "name": clean(r.get("name")), "roaster": clean(r.get("roaster")),
                "origin": ", ".join(x for x in (clean(r.get("origin_1")), clean(r.get("origin_2"))) if x) or None,
                "roast": clean(r.get("roast")), "acid": None, "body": None, "rating": num(r.get("rating")),
                "summary": clean(r.get("desc_1")), "text": join_text(r.get("desc_1"), r.get("desc_2"), r.get("desc_3")),
                "subs": {},
            }
            if ident in index:
                rows[index[ident]] = _fill_missing(rows[index[ident]], new)
            else:
                rows[f"{ident[0]}|{ident[1]}"] = new

    out = Normalized()
    if not rows:
        return out
    items = list(rows.items())
    df = pd.DataFrame([v for _, v in items])
    acid_q, body_q = to_quintile(df["acid"]), to_quintile(df["body"])
    for i, (rk, v) in enumerate(items):
        key = f"coffeereview:{rk}"
        is_decaf, decaf_process = detect_decaf(v["name"], v["text"])
        out.coffees.append(CoffeeRecord(
            key=key, name=v["name"] or "(unknown)", roaster=v["roaster"],
            origin_country=normalize_country(v["origin"]), origin_region=v["origin"],
            process=process_from_text(v["text"]), roast_level=normalize_roast(v["roast"]),
            is_decaf=is_decaf, decaf_process=decaf_process,
            acidity=opt_int(acid_q.iloc[i]), body=opt_int(body_q.iloc[i]),
            flavor_summary=v["summary"], source="coffeereview_kaggle", source_url=v["url"],
            collected_at=collected_at,
        ))
        if v["text"]:
            out.reviews.append(ReviewRecord(
                key=f"review:{key}", coffee_key=key, text=v["text"], rating=v["rating"], sub_scores=v["subs"],
                source="coffeereview_kaggle", source_url=v["url"], collected_at=collected_at,
            ))
    return out


# --- CQI -------------------------------------------------------------------
_CQI_2018 = {"country": "Country.of.Origin", "region": "Region", "farm": "Farm.Name",
             "process": "Processing.Method", "acidity": "Acidity", "body": "Body",
             "owner": "Owner", "company": "Company"}
_CQI_ROBUSTA = {**_CQI_2018, "acidity": "Salt...Acid", "body": "Mouthfeel"}
_CQI_2023 = {"country": "Country of Origin", "region": "Region", "farm": "Farm Name",
             "process": "Processing Method", "acidity": "Acidity", "body": "Body",
             "owner": "Owner", "company": "Company"}
_CQI_FILES = {
    "arabica_2018.csv": (_CQI_2018, "https://github.com/jldbc/coffee-quality-database"),
    "robusta_2018.csv": (_CQI_ROBUSTA, "https://github.com/jldbc/coffee-quality-database"),
    "arabica_2023.csv": (_CQI_2023, "https://github.com/fatih-boyar/coffee-quality-data-CQI"),
}


def normalize_cqi(snap: Path, collected_at: str) -> Normalized:
    rows = []
    for filename, (cols, url) in _CQI_FILES.items():
        p = snap / filename
        if not p.exists():
            continue
        for i, r in enumerate(_records(p)):
            g = {k: clean(r.get(c)) for k, c in cols.items()}
            rows.append({"key": f"cqi:{p.stem}:{i}", "url": url, **g,
                         "acid": num(g["acidity"]), "body_n": num(g["body"])})
    out = Normalized()
    if not rows:
        return out
    df = pd.DataFrame(rows)
    acid_q, body_q = to_quintile(df["acid"]), to_quintile(df["body_n"])
    for i, v in enumerate(rows):
        is_decaf, decaf_process = detect_decaf(v["owner"], v["company"], v["farm"])
        out.coffees.append(CoffeeRecord(
            key=v["key"], name=" ".join(x for x in (v["country"], v["region"], v["farm"]) if x) or "CQI sample",
            origin_country=normalize_country(v["country"]), origin_region=v["region"],
            process=normalize_process(v["process"]), is_decaf=is_decaf, decaf_process=decaf_process,
            acidity=opt_int(acid_q.iloc[i]), body=opt_int(body_q.iloc[i]),
            source="cqi", source_url=v["url"], collected_at=collected_at,
        ))
    return out


# --- RoasterDB sample --------------------------------------------------------
def parse_sca_nodes(s: str) -> list[str]:
    tags: list[str] = []
    for part in re.split(r"[;|]", s or ""):
        leaf = part.split(">")[-1].strip().lower()
        if leaf and leaf not in tags:
            tags.append(leaf)
    return tags[:6]


def normalize_roasterdb(snap: Path, collected_at: str) -> Normalized:
    out = Normalized()
    p = snap / "roasterdb_sample.csv"
    if not p.exists():
        return out
    for r in _records(p):
        notes = clean(r.get("tasting_notes_sca_nodes"))
        title = clean(r.get("title")) or "(unknown)"
        is_decaf, decaf_process = detect_decaf(title, notes)
        out.coffees.append(CoffeeRecord(
            key=f"roasterdb:{clean(r.get('product_id'))}", name=title, roaster=clean(r.get("source_roaster")),
            origin_country=normalize_country(r.get("origin_country")), origin_region=clean(r.get("origin_region")),
            process=normalize_process(r.get("process_method")), roast_level=normalize_roast(r.get("roast_level")),
            is_decaf=is_decaf, decaf_process=decaf_process, flavor_tags=parse_sca_nodes(notes or ""),
            flavor_summary=notes, source="roasterdb", source_url=clean(r.get("source_url")),
            collected_at=collected_at,
        ))
    return out


# --- SCA flavor wheel ------------------------------------------------------
def normalize_sca(snap: Path, collected_at: str, ko_path: Path | None = None) -> Normalized:
    out = Normalized()
    p = snap / "sca_coffee_flavors.json"
    if not p.exists():
        return out
    ko_path = ko_path or settings.CURATED_DIR / "sca_ko.yaml"
    ko = yaml.safe_load(ko_path.read_text(encoding="utf-8")) if ko_path.exists() else {}

    def walk(node, path: str, level: int) -> None:
        if isinstance(node, dict):
            children = list(node.items())
        elif isinstance(node, list):
            children = [(x, None) for x in node]
        else:
            return
        for name, child in children:
            here = f"{path}>{name}" if path else name
            out.taxonomy.append(TaxonomyNode(
                key=f"sca:{here}", parent_key=f"sca:{path}" if path else None,
                level=level, name_en=name, name_ko=ko.get(here),
            ))
            if child is not None:
                walk(child, here, level + 1)

    walk(json.loads(p.read_text(encoding="utf-8")), "", 1)
    return out
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_normalize_datasets.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add pipeline/normalize data/curated/sca_ko.yaml tests/test_normalize_datasets.py
git commit -m "feat(normalize): coffeereview·CQI·RoasterDB·SCA 정규화"
```

---

### Task 7: 메뉴·브랜드·Shopify 정규화 + run_normalize

**Files:**
- Create: `pipeline/normalize/menus.py`, `data/curated/brands.yaml`, `tests/test_normalize_menus.py`
- Modify: `pipeline/normalize/__init__.py` (끝에 `run_normalize` 추가)

**Interfaces:**
- Consumes: `Normalized`, `latest_snapshot`, `normalize_*` (Task 6), `write_jsonl`
- Produces:
  - `normalize_starbucks(snap, collected_at)`, `normalize_mega(snap, collected_at)`, `normalize_paik(snap, collected_at)` → `menu_items` (키 `menu:<brand>:<id|name>`, `brand_key` `brand:starbucks|brand:mega|brand:paik`)
  - `normalize_shopify(snap, collected_at, shops: list[dict] | None = None)` → 원두 `coffees` + 설명 `reviews` (키 `shopify:<domain>:<handle>`)
  - `normalize_brands(curated_dir: Path) -> list[BrandRecord]`
  - `run_normalize(raw_root: Path, out_dir: Path, curated_dir: Path) -> dict[str, int]` — `out_dir/{coffees,reviews,brands,menu_items,taxonomy}.jsonl`
  - `NORMALIZERS: dict[str, Callable[[Path, str], Normalized]]` (collector 이름과 같은 키)

- [ ] **Step 1: `data/curated/brands.yaml` 작성** (2026-09-24 조사 결과. `notes`에 확인 수준을 적는다)

```yaml
# 프랜차이즈 브랜드 수기 정리. 공식 페이지 직접 확인은 [공식], 뉴스·검색 요약은 [뉴스] 로 표시.
- key: brand:starbucks
  name: 스타벅스
  decaf_available: true
  decaf_surcharge_krw: 300
  notes: "[공식] 대부분 음료 디카페인 에스프레소 샷 선택 가능(일부 제외), 블론드/미디엄/다크 로스트. [뉴스] 디카페인 +300원(2026-05)."
  source_url: https://www.starbucks.co.kr/menu/drink_list.do
  verified_at: "2026-09-24"
- key: brand:twosome
  name: 투썸플레이스
  decaf_available: true
  decaf_surcharge_krw: 200
  notes: "[뉴스] 원두 3종(블랙그라운드/아로마노트/디카페인). 아로마노트=에티오피아 중심+과테말라, 화사한 산미·플로럴. 공식 사이트는 봇 차단으로 미확인."
  source_url: https://www.twosome.co.kr
  verified_at: "2026-09-24"
- key: brand:mega
  name: 메가MGC커피
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[공식] 디카페인 메뉴 별도(아메리카노·라떼·메가리카노 등). 원두 설명은 '묵직한 바디감' 위주, 산미 정보 없음."
  source_url: https://www.mega-mgccoffee.com/menu/
  verified_at: "2026-09-24"
- key: brand:compose
  name: 컴포즈커피
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[뉴스] 디카페인 아메리카노. 콜롬비아 슈가케인 공법, 묵직한 바디·견과·다크초콜릿(산미 낮은 편). 공식 메뉴는 캡차로 미확인."
  source_url: https://composecoffee.com
  verified_at: "2026-09-24"
- key: brand:paik
  name: 빽다방
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[공식] 디카페인 메뉴 별도(일부 매장만 판매), 전 메뉴 카페인 mg 표기. [뉴스] 일반 원두 브라질 세라도."
  source_url: https://paikdabang.com/menu/menu_coffee/
  verified_at: "2026-09-24"
- key: brand:ediya
  name: 이디야커피
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[뉴스] 모든 커피 메뉴 디카페인 변경 가능. 콜롬비아 싱글오리진 워터 프로세스."
  source_url: https://www.ediya.com/contents/drink.html
  verified_at: "2026-09-24"
- key: brand:hollys
  name: 할리스
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[공식] 디카페인 콜드브루 계열(라떼, 아샷추 등), '할리스 블렌딩 디카페인 원액 — 깔끔한 후미, 은은한 단맛'."
  source_url: https://www.hollys.co.kr/menu/espresso.do
  verified_at: "2026-09-24"
- key: brand:paulbassett
  name: 폴바셋
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[공식] Decaf 카테고리(아메리카노·라떼·플랫화이트 등), 시그니처 블렌드=브라질+에티오피아 '밝은 산미, 초콜릿', 디카페인 아메리카노 카페인 10mg 이하. [뉴스] 디카페인 블렌드도 브라질+에티오피아."
  source_url: https://www.baristapaulbassett.co.kr/menu/List.pb?cid1=A&cid2=F
  verified_at: "2026-09-24"
- key: brand:bluebottle
  name: 블루보틀
  decaf_available: true
  decaf_surcharge_krw: null
  notes: "[공식] 디카페인 원두 Night Light(미디엄): 크렘브륄레·바닐라·키라임. [뉴스] 싱글오리진 에스프레소 +1,100원."
  source_url: https://kr.bluebottlecoffee.com
  verified_at: "2026-09-24"
- key: brand:coffeebean
  name: 커피빈
  decaf_available: true
  decaf_surcharge_krw: 500
  notes: "[공식] 원두 가이드 로스트 단계 분류, 디카페인 원두 Espresso Roast Blend Decaf. [뉴스] 디카페인 변경 500원(2026-01)."
  source_url: https://www.coffeebeankorea.com/product/bean_guide.asp
  verified_at: "2026-09-24"
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_normalize_menus.py`

```python
import json

from pipeline.collect import run_collect
from pipeline.normalize import run_normalize
from pipeline.normalize.menus import (
    normalize_brands, normalize_mega, normalize_paik, normalize_shopify, normalize_starbucks,
)
from pipeline.records import BrandRecord, MenuItemRecord, read_jsonl

MEGA_HTML = """
<ul><li><a class="inner_modal_open"></a>
 <div class="inner_modal"><div class="cont_text_box">
  <div class="cont_text inner_modal_title">
   <div class="cont_text_inner cont_text_title"><b>디카페인 아메리카노</b></div>
   <div class="cont_text_inner cont_text_info">Decaf Americano</div></div>
 </div><div class="cont_list"><ul><li>당류 0g</li><li>카페인 11.4mg</li></ul></div></div>
</li></ul>
"""
PAIK_HTML = """
<div class="hover"><h3 class="font-bl">원조커피(ICED)</h3><div class="menu_tit2">ORIGINAL</div>
 <ul class="ingredient_table"><li><div>칼로리 (kcal)</div><div>633.8</div></li>
 <li><div>카페인 (mg)</div><div>346</div></li></ul></div>
<div class="hover"><h3 class="font-bl">원조커피(ICED)</h3><div class="menu_tit2">ORIGINAL</div></div>
<div class="hover"><h3 class="font-bl">디카페인 아메리카노</h3>
 <ul class="ingredient_table"><li><div>카페인 (mg)</div><div>1.9</div></li></ul></div>
"""


def test_starbucks(tmp_path):
    (tmp_path / "W0000003.json").write_text(json.dumps({"list": [
        {"product_CD": "1", "product_NM": "아메리카노", "product_ENGNM": "", "cate_NAME": "아메리카노", "caffeine": "150"},
        {"product_CD": "2", "product_NM": "디카페인 카페 라떼", "cate_NAME": "라떼", "caffeine": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    items = normalize_starbucks(tmp_path, "2026-09-24").menu_items
    assert [(i.key, i.caffeine_mg, i.is_decaf, i.decaf_option) for i in items] == [
        ("menu:starbucks:1", 150.0, False, True), ("menu:starbucks:2", None, True, True)]
    assert items[0].name_en is None


def test_mega(tmp_path):
    (tmp_path / "page_1.html").write_text(MEGA_HTML, encoding="utf-8")
    [item] = normalize_mega(tmp_path, "2026-09-24").menu_items
    assert (item.name, item.name_en, item.caffeine_mg, item.is_decaf) == ("디카페인 아메리카노", "Decaf Americano", 11.4, True)


def test_paik_dedupes_and_reads_caffeine(tmp_path):
    (tmp_path / "coffee.html").write_text(PAIK_HTML, encoding="utf-8")
    items = {i.name: i for i in normalize_paik(tmp_path, "2026-09-24").menu_items}
    assert set(items) == {"원조커피(ICED)", "디카페인 아메리카노"}
    assert items["원조커피(ICED)"].caffeine_mg == 346.0
    assert items["디카페인 아메리카노"].is_decaf is True


def test_shopify_keeps_bean_products(tmp_path):
    (tmp_path / "shop.test.json").write_text(json.dumps({"products": [
        {"handle": "night-light", "title": "나이트 라이트 디카페인 원두", "product_type": "원두",
         "body_html": "<p>콜롬비아 디카페인. 키라임, 바닐라.</p>"},
        {"handle": "mug", "title": "머그", "product_type": "컵과 머그", "body_html": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    n = normalize_shopify(tmp_path, "2026-09-24",
                          shops=[{"domain": "shop.test", "roaster": "Shop", "product_types": ["원두"]}])
    [c] = n.coffees
    assert (c.key, c.roaster, c.is_decaf, c.origin_country) == ("shopify:shop.test:night-light", "Shop", True, "Colombia")
    assert n.reviews[0].text == "콜롬비아 디카페인. 키라임, 바닐라."


def test_normalize_brands_reads_curated_file():
    from pipeline import settings
    brands = normalize_brands(settings.CURATED_DIR)
    assert {b.key for b in brands} >= {"brand:starbucks", "brand:mega", "brand:paik"}


def test_run_normalize_writes_all_files(tmp_path):
    from dataclasses import dataclass

    @dataclass
    class FakeStarbucks:
        name: str = "starbucks"

        def collect(self, out_dir, http):
            p = out_dir / "W0000003.json"
            p.write_text('{"list": [{"product_CD": "1", "product_NM": "아메리카노", "caffeine": "150"}]}', encoding="utf-8")
            return [p]

    raw = tmp_path / "raw"
    run_collect([FakeStarbucks()], raw, None, "2026-09-24")
    curated = tmp_path / "curated"
    curated.mkdir()
    (curated / "brands.yaml").write_text(
        "- {key: 'brand:starbucks', name: 스타벅스, decaf_available: true, verified_at: '2026-09-24'}\n", encoding="utf-8")
    counts = run_normalize(raw, tmp_path / "norm", curated)
    assert counts["menu_items"] == 1 and counts["brands"] == 1 and counts["src:starbucks"] == 1
    assert read_jsonl(tmp_path / "norm" / "menu_items.jsonl", MenuItemRecord)[0].brand_key == "brand:starbucks"
    assert read_jsonl(tmp_path / "norm" / "brands.jsonl", BrandRecord)[0].name == "스타벅스"
    assert (tmp_path / "norm" / "coffees.jsonl").exists()
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_normalize_menus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.normalize.menus'`

- [ ] **Step 4: `pipeline/normalize/menus.py` 구현**

```python
import json
import re
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from pipeline import settings
from pipeline.normalize import Normalized
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord
from pipeline.rules import clean, detect_decaf, normalize_country, normalize_roast, num, process_from_text

STARBUCKS_DECAF_OPTION_CODES = {"W0000003"}  # espresso drinks accept a decaf shot


def normalize_starbucks(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("W*.json")):
        for it in json.loads(p.read_text(encoding="utf-8"))["list"]:
            name = clean(it.get("product_NM"))
            if not name:
                continue
            key = f"menu:starbucks:{clean(it.get('product_CD')) or name}"
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:starbucks", name=name, name_en=clean(it.get("product_ENGNM")),
                category=clean(it.get("cate_NAME")), is_decaf=detect_decaf(name)[0],
                decaf_option=p.stem in STARBUCKS_DECAF_OPTION_CODES, caffeine_mg=num(it.get("caffeine")),
                source_url=f"https://www.starbucks.co.kr/upload/json/menu/{p.stem}.js", collected_at=collected_at,
            )
    return Normalized(menu_items=list(items.values()))


def _soup(p: Path) -> BeautifulSoup:
    return BeautifulSoup(p.read_text(encoding="utf-8"), "lxml")


def normalize_mega(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("page_*.html")):
        for modal in _soup(p).select("div.inner_modal"):
            name_el = modal.select_one(".cont_text_title b")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            en = modal.select_one(".inner_modal_title .cont_text_info")
            m = re.search(r"카페인\s*([\d.]+)\s*mg", modal.get_text(" ", strip=True))
            key = f"menu:mega:{name}"
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:mega", name=name, name_en=en.get_text(strip=True) if en else None,
                category="커피", is_decaf=detect_decaf(name)[0], caffeine_mg=float(m.group(1)) if m else None,
                source_url="https://www.mega-mgccoffee.com/menu/?menu_category1=1&menu_category2=1",
                collected_at=collected_at,
            )
    return Normalized(menu_items=list(items.values()))


def normalize_paik(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    p = snap / "coffee.html"
    if not p.exists():
        return Normalized()
    for hv in _soup(p).select("div.hover"):
        h3 = hv.select_one("h3")
        if not h3:
            continue
        name = h3.get_text(strip=True)
        key = f"menu:paik:{name}"
        caffeine = None
        for li in hv.select("ul.ingredient_table li"):
            divs = li.find_all("div")
            if len(divs) >= 2 and "카페인" in divs[0].get_text():
                caffeine = num(divs[1].get_text(strip=True))
        if key in items and caffeine is None:
            continue  # the recommendation slider repeats items without nutrition rows
        en = hv.select_one(".menu_tit2")
        items[key] = MenuItemRecord(
            key=key, brand_key="brand:paik", name=name, name_en=en.get_text(strip=True) if en else None,
            category="커피", is_decaf=detect_decaf(name)[0], caffeine_mg=caffeine,
            source_url="https://paikdabang.com/menu/menu_coffee/", collected_at=collected_at,
        )
    return Normalized(menu_items=list(items.values()))


def _html_text(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text("\n", strip=True)


def normalize_shopify(snap: Path, collected_at: str, shops: list[dict] | None = None) -> Normalized:
    shops = shops if shops is not None else settings.load_config("sources.yaml")["shopify"]
    by_domain = {s["domain"]: s for s in shops}
    out = Normalized()
    for p in sorted(snap.glob("*.json")):
        if p.name == "manifest.json":
            continue
        shop = by_domain.get(p.stem, {"domain": p.stem, "roaster": p.stem, "product_types": []})
        for prod in json.loads(p.read_text(encoding="utf-8"))["products"]:
            if prod.get("product_type") not in shop["product_types"]:
                continue
            title = clean(prod.get("title")) or "(unknown)"
            text = _html_text(prod.get("body_html"))
            key = f"shopify:{p.stem}:{prod.get('handle')}"
            url = f"https://{p.stem}/products/{prod.get('handle')}"
            is_decaf, decaf_process = detect_decaf(title, text)
            out.coffees.append(CoffeeRecord(
                key=key, name=title, roaster=shop["roaster"],
                origin_country=normalize_country(f"{title} {text}"),
                process=process_from_text(f"{title} {text}"), roast_level=normalize_roast(text),
                is_decaf=is_decaf, decaf_process=decaf_process,
                flavor_summary=text.split("\n")[0] if text else None,
                source="shopify", source_url=url, collected_at=collected_at,
            ))
            if text:
                out.reviews.append(ReviewRecord(key=f"review:{key}", coffee_key=key, text=text,
                                                source="shopify", source_url=url, collected_at=collected_at))
    return out


def normalize_brands(curated_dir: Path) -> list[BrandRecord]:
    p = curated_dir / "brands.yaml"
    if not p.exists():
        return []
    return [BrandRecord.model_validate(b) for b in yaml.safe_load(p.read_text(encoding="utf-8"))]
```

- [ ] **Step 5: `run_normalize` 추가** — `pipeline/normalize/__init__.py` 끝에

```python
from pathlib import Path  # noqa: E402

from pipeline.collect import latest_snapshot  # noqa: E402
from pipeline.records import write_jsonl  # noqa: E402


def _normalizers():
    from pipeline.normalize.datasets import normalize_coffeereview, normalize_cqi, normalize_roasterdb, normalize_sca
    from pipeline.normalize.menus import normalize_mega, normalize_paik, normalize_shopify, normalize_starbucks

    return {
        "coffeereview_kaggle": normalize_coffeereview, "cqi": normalize_cqi, "roasterdb": normalize_roasterdb,
        "sca_wheel": normalize_sca, "starbucks": normalize_starbucks, "mega": normalize_mega,
        "paik": normalize_paik, "shopify": normalize_shopify,
    }


def _dedupe(records):
    seen, out = set(), []
    for r in records:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out


def run_normalize(raw_root: Path, out_dir: Path, curated_dir: Path) -> dict[str, int]:
    from pipeline.normalize.menus import normalize_brands

    total, per_source = Normalized(), {}
    for name, fn in _normalizers().items():
        snap = latest_snapshot(raw_root, name)
        if snap is None:
            per_source[f"src:{name}"] = 0
            continue
        n = fn(snap, snap.name)
        total.extend(n)
        per_source[f"src:{name}"] = n.size()
    total.brands = normalize_brands(curated_dir)
    brand_keys = {b.key for b in total.brands}
    total.menu_items = [m for m in total.menu_items if m.brand_key in brand_keys]
    counts = {}
    for field_name in ("coffees", "reviews", "brands", "menu_items", "taxonomy"):
        records = _dedupe(getattr(total, field_name))
        counts[field_name] = write_jsonl(out_dir / f"{field_name}.jsonl", records)
    return {**counts, **per_source}
```

`NORMALIZERS`는 순환 import를 피하려고 함수 `_normalizers()`로 제공한다(Interfaces의 `NORMALIZERS` = `_normalizers()` 반환값).

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_normalize_menus.py tests/test_normalize_datasets.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add pipeline/normalize data/curated/brands.yaml tests/test_normalize_menus.py
git commit -m "feat(normalize): 프랜차이즈 메뉴·브랜드·Shopify 원두 정규화와 run_normalize"
```

---

### Task 8: LLM 클라이언트 + 임베더

**Files:**
- Create: `config/models.yaml`, `pipeline/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `settings.load_config`
- Produces (`pipeline.llm`):
  - `LLMError(Exception)`
  - `Target(provider: str, base_url: str, api_key: str | None, model: str, timeout: float, max_tokens: int = 1024, extra: dict | None = None)`
  - `LLMClient(primary: Target, fallback: Target | None = None, transport=None, sleep=time.sleep)` — `.chat(messages: list[dict]) -> str`, `.chat_json(messages, schema: type[BaseModel]) -> BaseModel`, 속성 `.calls: int`, `.last_model: str | None`
  - `Embedder(target: Target, transport=None, sleep=time.sleep)` — `.embed(texts: list[str]) -> list[list[float]]`
  - `extract_json(text: str) -> dict`
  - `load_targets(task: str) -> tuple[Target, Target | None]`, `client_for(task: str, **kw) -> LLMClient`, `embedder_for(task: str = "embed", **kw) -> Embedder`

- [ ] **Step 1: `config/models.yaml` 작성**

```yaml
providers:
  ollama:
    base_url: http://localhost:11434/v1
    api_key_env: null
  nvidia:
    base_url: https://integrate.api.nvidia.com/v1
    api_key_env: NVIDIA_API_KEY

tasks:
  enrich:
    provider: ollama
    model: "qwen3.5:9b"
    timeout: 180
    max_tokens: 2048
    extra: {reasoning_effort: "none"}
  embed:
    provider: ollama
    model: bge-m3
    timeout: 120
  vision:
    provider: nvidia
    model: meta/llama-3.2-11b-vision-instruct
    timeout: 30
    max_tokens: 800
    fallback: {provider: ollama, model: "qwen3.5:9b", timeout: 120, max_tokens: 2048}
  answer:
    provider: nvidia
    model: deepseek-ai/deepseek-v4.1-flash
    timeout: 30
    max_tokens: 1500
    fallback: {provider: ollama, model: "qwen3.5:9b", timeout: 120, max_tokens: 2048}
  # gold-set labeller: must differ from the enrich model, so no local fallback
  judge:
    provider: nvidia
    model: deepseek-ai/deepseek-v4.1-flash
    timeout: 120
    max_tokens: 3000
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_llm.py`

```python
import json

import httpx
import pytest
from pydantic import BaseModel

from pipeline.llm import Embedder, LLMClient, LLMError, Target, extract_json, load_targets


def target(model, provider="p"):
    return Target(provider, "http://llm.test/v1", "k", model, 5.0)


def transport(fn):
    def handler(request):
        return fn(json.loads(request.content), request)
    return httpx.MockTransport(handler)


def reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_chat_sends_openai_payload_and_returns_content():
    seen = {}

    def fn(body, req):
        seen.update(body=body, url=str(req.url), auth=req.headers.get("authorization"))
        return reply("hi")

    c = LLMClient(target("m"), transport=transport(fn))
    assert c.chat([{"role": "user", "content": "x"}]) == "hi"
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "m" and seen["body"]["temperature"] == 0
    assert c.calls == 1 and c.last_model == "m"


def test_fallback_on_error_timeout_and_empty_content():
    def fn(body, req):
        if body["model"] == "err":
            return httpx.Response(500, text="boom")
        if body["model"] == "slow":
            raise httpx.ReadTimeout("slow", request=req)
        if body["model"] == "empty":
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "..."}}]})
        return reply("from-fallback")

    for primary in ("err", "slow", "empty"):
        c = LLMClient(target(primary), fallback=target("fb"), transport=transport(fn))
        assert c.chat([{"role": "user", "content": "x"}]) == "from-fallback"
        assert c.last_model == "fb"


def test_error_without_fallback_raises():
    c = LLMClient(target("err"), transport=transport(lambda b, r: httpx.Response(500)))
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "x"}])


def test_retries_429_with_backoff():
    calls, sleeps = [], []

    def fn(body, req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else reply("ok")

    c = LLMClient(target("m"), transport=transport(fn), sleep=sleeps.append)
    assert c.chat([{"role": "user", "content": "x"}]) == "ok"
    assert sleeps == [1, 2]


class Out(BaseModel):
    x: int


def test_chat_json_retries_once_on_bad_json():
    answers = iter(["not json", '<think>hmm</think> {"x": 3}'])
    c = LLMClient(target("m"), transport=transport(lambda b, r: reply(next(answers))))
    assert c.chat_json([{"role": "user", "content": "x"}], Out) == Out(x=3)


def test_chat_json_gives_up_after_retry():
    c = LLMClient(target("m"), transport=transport(lambda b, r: reply('{"x": "nope"}')))
    with pytest.raises(LLMError):
        c.chat_json([{"role": "user", "content": "x"}], Out)


def test_extract_json_finds_object():
    assert extract_json('Sure! ```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}


def test_embedder_orders_by_index():
    def fn(body, req):
        assert body["input"] == ["a", "b"]
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})

    e = Embedder(target("bge-m3"), transport=transport(fn))
    assert e.embed(["a", "b"]) == [[1.0], [2.0]]


def test_load_targets_reads_config(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")
    primary, fallback = load_targets("vision")
    assert (primary.provider, primary.api_key, primary.timeout) == ("nvidia", "secret", 30.0)
    assert (fallback.provider, fallback.model, fallback.api_key) == ("ollama", "qwen3.5:9b", None)
    assert load_targets("embed")[1] is None
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.llm'`

- [ ] **Step 4: 구현** — `pipeline/llm.py`

```python
"""One OpenAI-compatible client for local Ollama and the NVIDIA API catalog."""
import json
import os
import re
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from pipeline import settings


class LLMError(Exception):
    pass


@dataclass
class Target:
    provider: str
    base_url: str
    api_key: str | None
    model: str
    timeout: float
    max_tokens: int = 1024
    extra: dict | None = None


def _post(target: Target, path: str, payload: dict, transport, sleep, max_retries: int = 3) -> dict:
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    with httpx.Client(base_url=target.base_url, timeout=target.timeout, transport=transport) as client:
        for attempt in range(max_retries + 1):
            try:
                r = client.post(path, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMError(f"timeout after {target.timeout}s: {target.model}") from e
            except httpx.HTTPError as e:
                raise LLMError(f"{type(e).__name__}: {e}") from e
            if r.status_code == 429 and attempt < max_retries:
                sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code} from {target.model}: {r.text[:200]}")
            return r.json()
    raise LLMError(f"rate limited: {target.model}")


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


class LLMClient:
    def __init__(self, primary: Target, fallback: Target | None = None, transport=None, sleep=time.sleep):
        self.primary, self.fallback = primary, fallback
        self._transport, self._sleep = transport, sleep
        self.calls = 0
        self.last_model: str | None = None

    def _chat_once(self, target: Target, messages: list[dict]) -> str:
        payload = {"model": target.model, "messages": messages, "max_tokens": target.max_tokens, "temperature": 0}
        if target.extra:
            payload.update(target.extra)
        self.calls += 1
        data = _post(target, "/chat/completions", payload, self._transport, self._sleep)
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not content or not content.strip():
            raise LLMError(f"empty content from {target.model}")
        self.last_model = target.model
        return content

    def chat(self, messages: list[dict]) -> str:
        try:
            return self._chat_once(self.primary, messages)
        except LLMError:
            if self.fallback is None:
                raise
            return self._chat_once(self.fallback, messages)

    def chat_json(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        last: Exception | None = None
        for _ in range(2):
            raw = self.chat(messages)
            try:
                return schema.model_validate(extract_json(raw))
            except (ValueError, ValidationError) as e:
                last = e
        raise LLMError(f"invalid JSON after retry: {last}")


class Embedder:
    def __init__(self, target: Target, transport=None, sleep=time.sleep):
        self.target, self._transport, self._sleep = target, transport, sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        data = _post(self.target, "/embeddings", {"model": self.target.model, "input": texts},
                     self._transport, self._sleep)
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


def load_targets(task: str) -> tuple[Target, Target | None]:
    cfg = settings.load_config("models.yaml")
    spec = cfg["tasks"][task]

    def make(s: dict) -> Target:
        p = cfg["providers"][s["provider"]]
        key = os.getenv(p["api_key_env"]) if p.get("api_key_env") else None
        return Target(s["provider"], p["base_url"], key, s["model"], float(s.get("timeout", 30)),
                      int(s.get("max_tokens", 1024)), s.get("extra"))

    return make(spec), (make(spec["fallback"]) if spec.get("fallback") else None)


def client_for(task: str, **kw) -> LLMClient:
    primary, fallback = load_targets(task)
    return LLMClient(primary, fallback, **kw)


def embedder_for(task: str = "embed", **kw) -> Embedder:
    return Embedder(load_targets(task)[0], **kw)
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_llm.py -v`
Expected: all passed

- [ ] **Step 6: 실제 모델 스모크 체크 (네트워크, 테스트 아님)**

```bash
uv run python -c "from pipeline.llm import client_for, embedder_for; import time; t=time.time(); c=client_for('enrich'); print(c.chat([{'role':'user','content':'Reply with JSON {\"ok\": true} only.'}]), c.last_model, f'{time.time()-t:.1f}s'); print(len(embedder_for().embed(['산미가 밝은 에티오피아'])[0]))"
```
Expected: `{"ok": true}` 비슷한 출력, `qwen3.5:9b`, 두 번째 줄 `1024`. 응답이 60초를 넘거나 `reasoning_effort` 때문에 HTTP 400이 나면 `config/models.yaml`의 `enrich.extra` 줄을 지우고 다시 실행해 기록한다.

- [ ] **Step 7: Commit**

```bash
git add config/models.yaml pipeline/llm.py tests/test_llm.py
git commit -m "feat(llm): Ollama·NVIDIA 공용 클라이언트(타임아웃·폴백·빈응답·429 처리)와 임베더"
```

---

### Task 9: enrich 단계 (규칙 우선 + LLM 보강 + 캐시)

**Files:**
- Create: `pipeline/enrich.py`, `tests/test_enrich.py`

**Interfaces:**
- Consumes: `read_jsonl/write_jsonl`, `CoffeeRecord`, `ReviewRecord`, `TaxonomyNode`, `detect_decaf`, `LLMClient.chat_json`, `LLMError`
- Produces:
  - `EnrichOutput(flavor_tags: list[str] = [], acidity/body/sweetness: int | None (1..5))`
  - `tag_vocab(taxonomy: list[TaxonomyNode]) -> list[str]` (level ≥ 2 의 `name_en` 소문자)
  - `rule_tags(text: str, vocab: list[str], limit: int = 6) -> list[str]`
  - `coffee_texts(reviews: list[ReviewRecord]) -> dict[str, str]`
  - `needs_llm(c: CoffeeRecord, text: str) -> bool`
  - `run_enrich(norm_dir: Path, out_dir: Path, client, limit: int | None = None, retry_failed: bool = False) -> dict[str, int]` — `out_dir/coffees.jsonl`, `out_dir/cache.jsonl` (라인: `{"key", "hash", "status": "ok"|"failed", "output"|"error", "model"}`)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_enrich.py`

```python
import json

from pipeline.enrich import EnrichOutput, needs_llm, rule_tags, run_enrich, tag_vocab
from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl, write_jsonl

VOCAB = ["lemon", "chocolate", "dark chocolate", "black tea", "honey"]


def coffee(key, **kw):
    return CoffeeRecord(key=key, name=kw.pop("name", key), source="t", collected_at="2026-09-24", **kw)


def test_rule_tags_prefers_longest_match():
    # ordered by position in the text; "chocolate" is swallowed by "dark chocolate"
    assert rule_tags("Dark chocolate, lemon zest and black tea.", VOCAB) == ["dark chocolate", "lemon", "black tea"]
    assert rule_tags("lemonade", VOCAB) == []


def test_tag_vocab_skips_level_one():
    tax = [TaxonomyNode(key="sca:fruity", level=1, name_en="fruity"),
           TaxonomyNode(key="sca:fruity>berry", parent_key="sca:fruity", level=2, name_en="Berry")]
    assert tag_vocab(tax) == ["berry"]


def test_needs_llm():
    assert needs_llm(coffee("a"), "text") is True
    assert needs_llm(coffee("a"), "") is False
    assert needs_llm(coffee("a", flavor_tags=["x"], acidity=3, body=3), "text") is False


class FakeClient:
    def __init__(self, fail_keys=()):
        self.fail_keys, self.seen, self.last_model = set(fail_keys), [], "fake"

    def chat_json(self, messages, schema):
        prompt = messages[-1]["content"]
        self.seen.append(prompt)
        if any(k in prompt for k in self.fail_keys):
            raise LLMError("boom")
        return schema(flavor_tags=["honey", "invented tag"], acidity=4, body=2, sweetness=3)


def setup_norm(tmp_path):
    norm = tmp_path / "norm"
    write_jsonl(norm / "coffees.jsonl", [
        coffee("c1", name="Kenya", acidity=5),                       # text has lemon -> rule tags, body missing -> LLM
        coffee("c2", name="FailMe"),                                 # LLM fails
        coffee("c3", name="Decaf Colombia", acidity=2, body=3),      # rule tags cover it -> no LLM
        coffee("c4", name="No text"),                                # no text -> no LLM
    ])
    write_jsonl(norm / "reviews.jsonl", [
        ReviewRecord(key="r1", coffee_key="c1", text="Bright lemon and black tea.", source="t", collected_at="x"),
        ReviewRecord(key="r2", coffee_key="c2", text="FailMe text", source="t", collected_at="x"),
        ReviewRecord(key="r3", coffee_key="c3", text="Swiss Water decaf, dark chocolate.", source="t", collected_at="x"),
    ])
    write_jsonl(norm / "taxonomy.jsonl", [TaxonomyNode(key=f"sca:x>{v}", level=2, name_en=v) for v in VOCAB])
    return norm


def test_run_enrich_rules_llm_failures_and_resume(tmp_path):
    norm, out = setup_norm(tmp_path), tmp_path / "enriched"
    client = FakeClient(fail_keys=["FailMe"])
    stats = run_enrich(norm, out, client)
    assert stats == {"coffees": 4, "llm_calls": 2, "llm_ok": 1, "llm_failed": 1}
    by = {c.key: c for c in read_jsonl(out / "coffees.jsonl", CoffeeRecord)}
    assert by["c1"].flavor_tags == ["lemon", "black tea"]      # rule tags kept, LLM tags not used
    assert (by["c1"].acidity, by["c1"].body, by["c1"].sweetness) == (5, 2, 3)  # rule value wins, LLM fills gaps
    assert by["c2"].body is None
    assert (by["c3"].is_decaf, by["c3"].decaf_process, by["c3"].flavor_tags) == (True, "swiss-water", ["dark chocolate"])
    assert by["c4"].flavor_tags == []

    # rerun: cached rows are not sent again, failed rows only with retry_failed
    client2 = FakeClient()
    assert run_enrich(norm, out, client2)["llm_calls"] == 0
    assert run_enrich(norm, out, client2, retry_failed=True)["llm_calls"] == 1
    last = [json.loads(l) for l in (out / "cache.jsonl").read_text(encoding="utf-8").splitlines()][-1]
    assert (last["key"], last["status"]) == ("c2", "ok")


def test_llm_only_tags_are_filtered_to_vocab(tmp_path):
    norm, out = tmp_path / "norm", tmp_path / "enriched"
    write_jsonl(norm / "coffees.jsonl", [coffee("c9")])
    write_jsonl(norm / "reviews.jsonl", [ReviewRecord(key="r", coffee_key="c9", text="nice cup", source="t", collected_at="x")])
    write_jsonl(norm / "taxonomy.jsonl", [TaxonomyNode(key="sca:x>honey", level=2, name_en="honey")])
    run_enrich(norm, out, FakeClient())
    assert read_jsonl(out / "coffees.jsonl", CoffeeRecord)[0].flavor_tags == ["honey"]


def test_limit_caps_llm_calls(tmp_path):
    norm = setup_norm(tmp_path)
    assert run_enrich(norm, tmp_path / "e", FakeClient(), limit=1)["llm_calls"] == 1


def test_enrich_output_validates_range():
    assert EnrichOutput(acidity=5).acidity == 5
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.enrich'`

- [ ] **Step 3: 구현** — `pipeline/enrich.py`

```python
import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl, write_jsonl
from pipeline.rules import detect_decaf


class EnrichOutput(BaseModel):
    flavor_tags: list[str] = Field(default_factory=list)
    acidity: int | None = Field(default=None, ge=1, le=5)
    body: int | None = Field(default=None, ge=1, le=5)
    sweetness: int | None = Field(default=None, ge=1, le=5)


SYSTEM = "You extract structured coffee flavor data from tasting notes. Reply with one JSON object only."
PROMPT = """Coffee: {name}
Tasting text:
{text}

Allowed flavor tags (SCA flavor wheel): {vocab}

Return JSON: {{"flavor_tags": [up to 6 tags from the allowed list], "acidity": 1-5 or null, "body": 1-5 or null, "sweetness": 1-5 or null}}
Scale: 1 = very low, 3 = moderate, 5 = very high. Use null when the text gives no evidence."""


def tag_vocab(taxonomy: list[TaxonomyNode]) -> list[str]:
    return sorted({n.name_en.lower() for n in taxonomy if n.level >= 2})


def rule_tags(text: str, vocab: list[str], limit: int = 6) -> list[str]:
    t = (text or "").lower()
    hits: list[tuple[int, str]] = []
    for term in sorted(vocab, key=len, reverse=True):
        m = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", t)
        if m and not any(term in h for _, h in hits):
            hits.append((m.start(), term))
    return [term for _, term in sorted(hits)][:limit]


def coffee_texts(reviews: list[ReviewRecord]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in reviews:
        out[r.coffee_key] = f"{out[r.coffee_key]}\n{r.text}" if r.coffee_key in out else r.text
    return out


def needs_llm(c: CoffeeRecord, text: str) -> bool:
    return bool(text) and (not c.flavor_tags or c.acidity is None or c.body is None)


def _apply_rules(c: CoffeeRecord, text: str, vocab: list[str]) -> CoffeeRecord:
    update: dict = {}
    if not c.flavor_tags and text:
        update["flavor_tags"] = rule_tags(text, vocab)
    if not c.is_decaf:
        is_decaf, process = detect_decaf(c.name, text)
        if is_decaf:
            update.update(is_decaf=True, decaf_process=process)
    return c.model_copy(update=update)


def _merge_llm(c: CoffeeRecord, o: EnrichOutput, vocab: set[str]) -> CoffeeRecord:
    update = {k: getattr(o, k) for k in ("acidity", "body", "sweetness") if getattr(c, k) is None and getattr(o, k)}
    if not c.flavor_tags:
        update["flavor_tags"] = [t.lower() for t in o.flavor_tags if t.lower() in vocab][:6]
    return c.model_copy(update=update)


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    cache = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            cache[e["key"]] = e  # later lines win
    return cache


def run_enrich(norm_dir: Path, out_dir: Path, client, limit: int | None = None, retry_failed: bool = False) -> dict[str, int]:
    coffees = read_jsonl(norm_dir / "coffees.jsonl", CoffeeRecord)
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    vocab = tag_vocab(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode))
    vocab_set = set(vocab)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "cache.jsonl"
    cache = _load_cache(cache_path)
    stats = {"coffees": 0, "llm_calls": 0, "llm_ok": 0, "llm_failed": 0}
    enriched = []
    with cache_path.open("a", encoding="utf-8") as cache_file:
        for c in coffees:
            text = texts.get(c.key) or c.flavor_summary or ""
            c = _apply_rules(c, text, vocab)
            if needs_llm(c, text):
                h = hashlib.sha1(f"{c.name}\n{text}".encode("utf-8")).hexdigest()
                entry = cache.get(c.key)
                fresh = entry is not None and entry["hash"] == h
                if not (fresh and (entry["status"] == "ok" or not retry_failed)) and (limit is None or stats["llm_calls"] < limit):
                    stats["llm_calls"] += 1
                    messages = [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": PROMPT.format(name=c.name, text=text[:3000], vocab=", ".join(vocab))}]
                    try:
                        o = client.chat_json(messages, EnrichOutput)
                        entry = {"key": c.key, "hash": h, "status": "ok", "output": o.model_dump(), "model": client.last_model}
                        stats["llm_ok"] += 1
                    except LLMError as e:
                        entry = {"key": c.key, "hash": h, "status": "failed", "error": str(e), "model": None}
                        stats["llm_failed"] += 1
                    cache[c.key] = entry
                    cache_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    cache_file.flush()
                if entry and entry["hash"] == h and entry["status"] == "ok":
                    c = _merge_llm(c, EnrichOutput(**entry["output"]), vocab_set)
            enriched.append(c)
    stats["coffees"] = write_jsonl(out_dir / "coffees.jsonl", enriched)
    return stats
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/enrich.py tests/test_enrich.py
git commit -m "feat(enrich): 규칙 우선 태깅·디카페인 판별, 빈 칸만 LLM 보강, 재개 가능한 캐시"
```

---

### Task 10: DB 스키마 + embed + load + 유사도 질의

**Files:**
- Create: `db/schema.sql`, `pipeline/db.py`, `pipeline/embed.py`, `pipeline/load.py`, `pipeline/query.py`, `tests/conftest.py`, `tests/test_embed.py`, `tests/test_db_load.py`

**Interfaces:**
- Consumes: records, `Embedder.embed`, enrich 산출물(`coffees.jsonl`, `cache.jsonl`)
- Produces:
  - `pipeline.db`: `connect(url: str | None = None) -> psycopg.Connection`, `apply_schema(conn)`, `reset_tables(conn)`, `TABLES: list[str]`
  - `pipeline.embed`: `embedding_text(c: CoffeeRecord, review_text: str | None) -> str`, `run_embed(enriched_dir: Path, norm_dir: Path, out_dir: Path, embedder, batch: int = 32) -> dict[str, int]` → `out_dir/embeddings.jsonl` (라인 `{"key", "hash", "vector"}`)
  - `pipeline.load`: `run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path) -> dict[str, int]`
  - `pipeline.query`: `to_vector_literal(vec) -> str`, `similar(conn, embedder, text: str, k: int = 5, decaf: bool | None = None) -> list[dict]`

- [ ] **Step 1: `db/schema.sql` 작성**

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS coffees (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  name text NOT NULL,
  roaster text,
  origin_country text,
  origin_region text,
  process text,
  roast_level text,
  is_decaf boolean NOT NULL DEFAULT false,
  decaf_process text,
  acidity smallint CHECK (acidity BETWEEN 1 AND 5),
  body smallint CHECK (body BETWEEN 1 AND 5),
  sweetness smallint CHECK (sweetness BETWEEN 1 AND 5),
  flavor_tags text[] NOT NULL DEFAULT '{}',
  flavor_summary text,
  embedding vector(1024),
  source text NOT NULL,
  source_url text,
  collected_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  coffee_id bigint NOT NULL REFERENCES coffees(id) ON DELETE CASCADE,
  text text NOT NULL,
  rating real,
  sub_scores jsonb NOT NULL DEFAULT '{}',
  source text NOT NULL,
  source_url text,
  collected_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS brands (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  name text NOT NULL,
  decaf_available boolean NOT NULL,
  decaf_surcharge_krw integer,
  default_bean_coffee_id bigint REFERENCES coffees(id),
  decaf_bean_coffee_id bigint REFERENCES coffees(id),
  notes text,
  source_url text,
  verified_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS menu_items (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  brand_id bigint NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  name text NOT NULL,
  name_en text,
  category text,
  is_decaf boolean NOT NULL DEFAULT false,
  decaf_option boolean NOT NULL DEFAULT false,
  caffeine_mg real,
  coffee_id bigint REFERENCES coffees(id),
  source_url text,
  collected_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS flavor_taxonomy (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  parent_id bigint REFERENCES flavor_taxonomy(id),
  level smallint NOT NULL,
  name_en text NOT NULL,
  name_ko text
);

CREATE TABLE IF NOT EXISTS enrich_log (
  row_ref text NOT NULL,
  stage text NOT NULL,
  status text NOT NULL CHECK (status IN ('ok', 'failed')),
  error text,
  model text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (row_ref, stage)
);

CREATE INDEX IF NOT EXISTS coffees_embedding_idx ON coffees USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS coffees_is_decaf_idx ON coffees (is_decaf);
CREATE INDEX IF NOT EXISTS menu_items_brand_idx ON menu_items (brand_id);
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/conftest.py`:
```python
import os

import psycopg
import pytest

from pipeline.db import apply_schema, reset_tables

TEST_URL = os.getenv("DATABASE_URL_TEST", "postgresql://coffee:coffee@localhost:5432/coffee_test")


@pytest.fixture
def db_conn():
    admin_url = TEST_URL.rsplit("/", 1)[0] + "/coffee"
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    with admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'coffee_test'").fetchone():
            admin.execute("CREATE DATABASE coffee_test")
    conn = psycopg.connect(TEST_URL)
    apply_schema(conn)
    reset_tables(conn)
    yield conn
    conn.close()
```

`tests/test_embed.py`:
```python
from pipeline.embed import embedding_text, run_embed
from pipeline.records import CoffeeRecord, ReviewRecord, write_jsonl


class FakeEmbedder:
    def __init__(self):
        self.batches = []

    def embed(self, texts):
        self.batches.append(list(texts))
        return [[float(len(t))] + [0.0] * 1023 for t in texts]


def coffee(key, **kw):
    return CoffeeRecord(key=key, name=kw.pop("name", key), source="t", collected_at="2026-09-24", **kw)


def test_embedding_text_includes_structure_and_review():
    c = coffee("a", name="Kenya AA", origin_country="Kenya", process="washed", is_decaf=True,
               decaf_process="swiss-water", flavor_tags=["lemon"], flavor_summary="Bright.")
    t = embedding_text(c, "Long review " * 400)
    assert t.startswith("Kenya AA | Kenya | washed | decaf swiss-water | lemon | Bright. | Long review")
    assert len(t) < 2000


def test_run_embed_caches_unchanged_rows(tmp_path):
    enriched, norm, out = tmp_path / "e", tmp_path / "n", tmp_path / "o"
    write_jsonl(enriched / "coffees.jsonl", [coffee("a"), coffee("b")])
    write_jsonl(norm / "reviews.jsonl", [ReviewRecord(key="r", coffee_key="a", text="txt", source="t", collected_at="x")])
    e = FakeEmbedder()
    assert run_embed(enriched, norm, out, e, batch=1) == {"embedded": 2, "cached": 0}
    assert len(e.batches) == 2
    write_jsonl(enriched / "coffees.jsonl", [coffee("a"), coffee("b", name="changed")])
    assert run_embed(enriched, norm, out, e) == {"embedded": 1, "cached": 1}
```

`tests/test_db_load.py`:
```python
import pytest

from pipeline.load import run_load
from pipeline.query import similar, to_vector_literal
from pipeline.records import (
    BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, write_jsonl,
)
import json

pytestmark = pytest.mark.db


def vec(i):
    v = [0.0] * 1024
    v[i] = 1.0
    return v


def setup_files(tmp_path):
    norm, enriched, embedded = tmp_path / "n", tmp_path / "e", tmp_path / "m"
    coffees = [
        CoffeeRecord(key="c1", name="Ethiopia Washed", origin_country="Ethiopia", is_decaf=False,
                     acidity=5, flavor_tags=["lemon"], source="t", collected_at="2026-09-24"),
        CoffeeRecord(key="c2", name="Ethiopia Decaf", origin_country="Ethiopia", is_decaf=True,
                     decaf_process="swiss-water", source="t", collected_at="2026-09-24"),
    ]
    write_jsonl(enriched / "coffees.jsonl", coffees)
    (enriched / "cache.jsonl").write_text(
        json.dumps({"key": "c1", "hash": "h", "status": "failed", "error": "x", "model": None}) + "\n", encoding="utf-8")
    embedded.mkdir(parents=True)
    (embedded / "embeddings.jsonl").write_text(
        "\n".join(json.dumps({"key": k, "hash": "h", "vector": vec(i)}) for i, k in enumerate(["c1", "c2"])) + "\n",
        encoding="utf-8")
    write_jsonl(norm / "reviews.jsonl", [ReviewRecord(key="r1", coffee_key="c1", text="t", source="t", collected_at="2026-09-24")])
    write_jsonl(norm / "brands.jsonl", [BrandRecord(key="brand:x", name="X", decaf_available=True, verified_at="2026-09-24")])
    write_jsonl(norm / "menu_items.jsonl", [MenuItemRecord(key="m1", brand_key="brand:x", name="아메리카노",
                                                           caffeine_mg=150, collected_at="2026-09-24")])
    write_jsonl(norm / "taxonomy.jsonl", [
        TaxonomyNode(key="sca:fruity", level=1, name_en="fruity", name_ko="과일"),
        TaxonomyNode(key="sca:fruity>berry", parent_key="sca:fruity", level=2, name_en="berry"),
    ])
    return norm, enriched, embedded


class FixedEmbedder:
    def embed(self, texts):
        return [vec(1) for _ in texts]


def test_load_and_query(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    counts = run_load(db_conn, norm, enriched, embedded)
    assert counts == {"coffees": 2, "reviews": 1, "brands": 1, "menu_items": 1, "flavor_taxonomy": 2, "enrich_log": 1}
    parent = db_conn.execute("SELECT p.key FROM flavor_taxonomy c JOIN flavor_taxonomy p ON c.parent_id = p.id").fetchone()
    assert parent == ("sca:fruity",)
    hits = similar(db_conn, FixedEmbedder(), "decaf ethiopia", k=2)
    assert [h["name"] for h in hits] == ["Ethiopia Decaf", "Ethiopia Washed"]
    assert [h["name"] for h in similar(db_conn, FixedEmbedder(), "x", decaf=False)] == ["Ethiopia Washed"]
    # reload is idempotent
    assert run_load(db_conn, norm, enriched, embedded)["coffees"] == 2


def test_to_vector_literal():
    assert to_vector_literal([1, 0.5]) == "[1.0,0.5]"
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_embed.py tests/test_db_load.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.db'` (conftest import)

- [ ] **Step 4: `pipeline/db.py` 구현**

```python
import psycopg

from pipeline import settings

SCHEMA_PATH = settings.ROOT / "db" / "schema.sql"
TABLES = ["menu_items", "brands", "reviews", "coffees", "flavor_taxonomy", "enrich_log"]


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or settings.DATABASE_URL)


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def reset_tables(conn: psycopg.Connection) -> None:
    conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()
```

- [ ] **Step 5: `pipeline/embed.py` 구현**

```python
import hashlib
import json
from pathlib import Path

from pipeline.enrich import coffee_texts
from pipeline.records import CoffeeRecord, ReviewRecord, read_jsonl

REVIEW_CHARS = 1500


def embedding_text(c: CoffeeRecord, review_text: str | None) -> str:
    parts = [
        c.name, c.origin_country, c.process, c.roast_level,
        f"decaf {c.decaf_process or ''}".strip() if c.is_decaf else None,
        ", ".join(c.flavor_tags) or None, c.flavor_summary,
        (review_text or "")[:REVIEW_CHARS] or None,
    ]
    return " | ".join(p for p in parts if p)


def run_embed(enriched_dir: Path, norm_dir: Path, out_dir: Path, embedder, batch: int = 32) -> dict[str, int]:
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    path = out_dir / "embeddings.jsonl"
    cache: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                cache[e["key"]] = e
    rows, todo = {}, []
    for c in coffees:
        text = embedding_text(c, texts.get(c.key))
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if c.key in cache and cache[c.key]["hash"] == h:
            rows[c.key] = cache[c.key]
        else:
            todo.append((c.key, h, text))
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        for (key, h, _), v in zip(chunk, embedder.embed([t for _, _, t in chunk])):
            rows[key] = {"key": key, "hash": h, "vector": v}
    out_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in coffees:
            f.write(json.dumps(rows[c.key]) + "\n")
    return {"embedded": len(todo), "cached": len(coffees) - len(todo)}
```

- [ ] **Step 6: `pipeline/load.py` 구현**

```python
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from pipeline.db import reset_tables
from pipeline.query import to_vector_literal
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, read_jsonl


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _ids(conn, table: str) -> dict[str, int]:
    return dict(conn.execute(f"SELECT key, id FROM {table}").fetchall())


def run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path) -> dict[str, int]:
    reset_tables(conn)
    cur = conn.cursor()

    taxonomy = sorted(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode), key=lambda t: t.level)
    for t in taxonomy:
        cur.execute(
            "INSERT INTO flavor_taxonomy (key, parent_id, level, name_en, name_ko) "
            "VALUES (%s, (SELECT id FROM flavor_taxonomy WHERE key = %s), %s, %s, %s)",
            (t.key, t.parent_key, t.level, t.name_en, t.name_ko))

    vectors = {e["key"]: e["vector"] for e in _read_lines(embedded_dir / "embeddings.jsonl")}
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    cur.executemany(
        "INSERT INTO coffees (key, name, roaster, origin_country, origin_region, process, roast_level, is_decaf,"
        " decaf_process, acidity, body, sweetness, flavor_tags, flavor_summary, embedding, source, source_url,"
        " collected_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s,%s)",
        [(c.key, c.name, c.roaster, c.origin_country, c.origin_region, c.process, c.roast_level, c.is_decaf,
          c.decaf_process, c.acidity, c.body, c.sweetness, c.flavor_tags, c.flavor_summary,
          to_vector_literal(vectors[c.key]) if c.key in vectors else None, c.source, c.source_url, c.collected_at)
         for c in coffees])
    coffee_ids = _ids(conn, "coffees")

    reviews = [r for r in read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord) if r.coffee_key in coffee_ids]
    cur.executemany(
        "INSERT INTO reviews (key, coffee_id, text, rating, sub_scores, source, source_url, collected_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        [(r.key, coffee_ids[r.coffee_key], r.text, r.rating, Jsonb(r.sub_scores), r.source, r.source_url,
          r.collected_at) for r in reviews])

    brands = read_jsonl(norm_dir / "brands.jsonl", BrandRecord)
    cur.executemany(
        "INSERT INTO brands (key, name, decaf_available, decaf_surcharge_krw, default_bean_coffee_id,"
        " decaf_bean_coffee_id, notes, source_url, verified_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, coffee_ids.get(b.default_bean_coffee_key),
          coffee_ids.get(b.decaf_bean_coffee_key), b.notes, b.source_url, b.verified_at) for b in brands])
    brand_ids = _ids(conn, "brands")

    items = [m for m in read_jsonl(norm_dir / "menu_items.jsonl", MenuItemRecord) if m.brand_key in brand_ids]
    cur.executemany(
        "INSERT INTO menu_items (key, brand_id, name, name_en, category, is_decaf, decaf_option, caffeine_mg,"
        " coffee_id, source_url, collected_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(m.key, brand_ids[m.brand_key], m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,
          m.caffeine_mg, coffee_ids.get(m.coffee_key), m.source_url, m.collected_at) for m in items])

    log = {e["key"]: e for e in _read_lines(enriched_dir / "cache.jsonl")}
    cur.executemany(
        "INSERT INTO enrich_log (row_ref, stage, status, error, model) VALUES (%s, 'enrich', %s, %s, %s)",
        [(k, e["status"], e.get("error"), e.get("model")) for k, e in log.items()])

    conn.commit()
    return {"coffees": len(coffees), "reviews": len(reviews), "brands": len(brands), "menu_items": len(items),
            "flavor_taxonomy": len(taxonomy), "enrich_log": len(log)}
```

- [ ] **Step 7: `pipeline/query.py` 구현**

```python
def to_vector_literal(vec) -> str:
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def similar(conn, embedder, text: str, k: int = 5, decaf: bool | None = None) -> list[dict]:
    v = to_vector_literal(embedder.embed([text])[0])
    where = "embedding IS NOT NULL" + (" AND is_decaf = %(decaf)s" if decaf is not None else "")
    sql = (
        "SELECT name, roaster, origin_country, process, is_decaf, decaf_process, acidity, body, flavor_tags,"
        f" 1 - (embedding <=> %(v)s::vector) AS score FROM coffees WHERE {where}"
        " ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s"
    )
    cur = conn.execute(sql, {"v": v, "k": k, "decaf": decaf})
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 8: DB 기동 후 통과 확인**

Docker Desktop이 켜져 있어야 한다(현재 꺼져 있음 — 사용자에게 실행 요청).

Run: `docker compose up -d db && uv run pytest tests/test_embed.py tests/test_db_load.py -v`
Expected: all passed (DB가 없으면 `test_db_load.py`의 db 테스트는 SKIPPED로 표시되며, 이 경우 Task를 완료로 표시하지 않는다)

- [ ] **Step 9: Commit**

```bash
git add db/schema.sql pipeline/db.py pipeline/embed.py pipeline/load.py pipeline/query.py tests/conftest.py tests/test_embed.py tests/test_db_load.py
git commit -m "feat(db): pgvector 스키마, 임베딩 캐시, 멱등 적재, 유사도 질의"
```

---

### Task 11: 품질 리포트 + CLI

**Files:**
- Create: `pipeline/report.py`, `pipeline/__main__.py`, `tests/test_cli.py`
- Test: `tests/test_db_load.py` (리포트 테스트 추가)

**Interfaces:**
- Consumes: 모든 이전 단계 함수, `ALL_COLLECTORS`, `client_for`, `embedder_for`, `connect`, `apply_schema`
- Produces:
  - `pipeline.report.build_report(conn, stage_stats: dict[str, dict]) -> str` (마크다운)
  - `pipeline.__main__.build_parser() -> argparse.ArgumentParser`, `main(argv: list[str] | None = None) -> int`
  - 명령: `run [--only STAGE]... [--source NAME]... [--limit N] [--retry-failed]`, `query TEXT [-k N] [--decaf|--no-decaf]`, `gold-sample [--n N] [--seed S]`, `gold-score`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli.py`:
```python
from pipeline.__main__ import STAGES, build_parser


def test_run_defaults_to_all_stages():
    a = build_parser().parse_args(["run"])
    assert a.cmd == "run" and a.only is None and STAGES == ["collect", "normalize", "enrich", "embed", "load"]


def test_run_options():
    a = build_parser().parse_args(["run", "--only", "collect", "--only", "normalize", "--source", "mega", "--limit", "5", "--retry-failed"])
    assert (a.only, a.source, a.limit, a.retry_failed) == (["collect", "normalize"], ["mega"], 5, True)


def test_query_decaf_flags():
    p = build_parser()
    assert p.parse_args(["query", "x"]).decaf is None
    assert p.parse_args(["query", "x", "--decaf"]).decaf is True
    assert p.parse_args(["query", "x", "--no-decaf", "-k", "3"]).decaf is False
```

`tests/test_db_load.py` 끝에 추가:
```python
from pipeline.report import build_report


def test_report_counts_and_missing_rates(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    md = build_report(db_conn, {"enrich": {"llm_calls": 1}})
    assert "| coffees | 2 |" in md
    assert "| decaf coffees | 1 |" in md
    assert "| body | 100.0% |" in md          # both coffees lack body
    assert "| acidity | 50.0% |" in md
    assert "| t | 2 |" in md                  # per-source count
    assert "enrich failed: 1" in md
    assert "llm_calls" in md
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_cli.py tests/test_db_load.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.__main__'` / `pipeline.report`

- [ ] **Step 3: `pipeline/report.py` 구현**

```python
import json

FIELDS = ["origin_country", "process", "roast_level", "acidity", "body", "sweetness", "flavor_tags", "embedding"]


def build_report(conn, stage_stats: dict[str, dict]) -> str:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    total = q("SELECT count(*) FROM coffees")
    lines = ["# 데이터 품질 리포트", "", "## 테이블", "", "| 항목 | 행 수 |", "|---|---|"]
    for t in ["coffees", "reviews", "brands", "menu_items", "flavor_taxonomy"]:
        lines.append(f"| {t} | {q(f'SELECT count(*) FROM {t}')} |")
    lines.append(f"| decaf coffees | {q('SELECT count(*) FROM coffees WHERE is_decaf')} |")
    lines.append(f"| decaf menu items | {q('SELECT count(*) FROM menu_items WHERE is_decaf')} |")

    lines += ["", "## 소스별 원두", "", "| source | 행 수 |", "|---|---|"]
    for source, n in conn.execute("SELECT source, count(*) FROM coffees GROUP BY source ORDER BY 2 DESC").fetchall():
        lines.append(f"| {source} | {n} |")

    lines += ["", "## 필드 결측률 (coffees)", "", "| field | missing |", "|---|---|"]
    for f in FIELDS:
        cond = "cardinality(flavor_tags) = 0" if f == "flavor_tags" else f"{f} IS NULL"
        missing = q(f"SELECT count(*) FROM coffees WHERE {cond}")
        rate = 100.0 * missing / total if total else 0.0
        lines.append(f"| {f} | {rate:.1f}% |")

    failed = q("SELECT count(*) FROM enrich_log WHERE status = 'failed'")
    lines += ["", "## enrich", "", f"enrich failed: {failed}",
              "", "## 단계 통계", "", "```json", json.dumps(stage_stats, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)
```

- [ ] **Step 4: `pipeline/__main__.py` 구현**

```python
import argparse
import datetime as dt
import json
import sys

from pipeline import settings

STAGES = ["collect", "normalize", "enrich", "embed", "load"]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run pipeline stages")
    r.add_argument("--only", choices=STAGES, action="append")
    r.add_argument("--source", action="append", help="collect only these sources")
    r.add_argument("--limit", type=int, help="max LLM calls in enrich")
    r.add_argument("--retry-failed", action="store_true")
    q = sub.add_parser("query", help="similarity search")
    q.add_argument("text")
    q.add_argument("-k", type=int, default=5)
    g = q.add_mutually_exclusive_group()
    g.add_argument("--decaf", dest="decaf", action="store_const", const=True, default=None)
    g.add_argument("--no-decaf", dest="decaf", action="store_const", const=False)
    gs = sub.add_parser("gold-sample", help="sample rows for the tagging gold set")
    gs.add_argument("--n", type=int, default=50)
    gs.add_argument("--seed", type=int, default=42)
    sub.add_parser("gold-score", help="score enrich output against the labelled gold set")
    return ap


def _run(a) -> int:
    stages = a.only or STAGES
    stats: dict[str, dict] = {}
    if "collect" in stages:
        from pipeline.collect import run_collect
        from pipeline.collect.registry import ALL_COLLECTORS
        from pipeline.http import PoliteClient

        cols = [c for c in ALL_COLLECTORS if not a.source or c.name in a.source]
        ms = run_collect(cols, settings.RAW_DIR, PoliteClient(), dt.date.today().isoformat())
        stats["collect"] = {m.source: ("ok" if m.ok else m.error) for m in ms}
    if "normalize" in stages:
        from pipeline.normalize import run_normalize
        stats["normalize"] = run_normalize(settings.RAW_DIR, settings.NORMALIZED_DIR, settings.CURATED_DIR)
    if "enrich" in stages:
        from pipeline.enrich import run_enrich
        from pipeline.llm import client_for
        stats["enrich"] = run_enrich(settings.NORMALIZED_DIR, settings.ENRICHED_DIR, client_for("enrich"),
                                     limit=a.limit, retry_failed=a.retry_failed)
    if "embed" in stages:
        from pipeline.embed import run_embed
        from pipeline.llm import embedder_for
        stats["embed"] = run_embed(settings.ENRICHED_DIR, settings.NORMALIZED_DIR, settings.EMBEDDED_DIR, embedder_for())
    for stage, s in stats.items():
        print(f"[{stage}] {json.dumps(s, ensure_ascii=False)}")
    if "load" in stages:
        from pipeline.db import apply_schema, connect
        from pipeline.load import run_load
        from pipeline.report import build_report

        with connect() as conn:
            apply_schema(conn)
            stats["load"] = run_load(conn, settings.NORMALIZED_DIR, settings.ENRICHED_DIR, settings.EMBEDDED_DIR)
            md = build_report(conn, stats)
        settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.REPORTS_DIR / f"quality_{dt.date.today().isoformat()}.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "run":
        return _run(a)
    if a.cmd == "query":
        from pipeline.db import connect
        from pipeline.llm import embedder_for
        from pipeline.query import similar

        with connect() as conn:
            for h in similar(conn, embedder_for(), a.text, k=a.k, decaf=a.decaf):
                print(f"{h['score']:.3f}  {h['name']} | {h['roaster']} | {h['origin_country']} | {h['process']} | "
                      f"decaf={h['is_decaf']} | acidity={h['acidity']} body={h['body']} | {', '.join(h['flavor_tags'])}")
        return 0
    if a.cmd == "gold-sample":
        from pipeline.gold import sample_gold
        n = sample_gold(settings.ENRICHED_DIR, settings.NORMALIZED_DIR, settings.EVAL_DIR / "gold_enrich.csv", n=a.n, seed=a.seed)
        print(f"wrote {n} rows to {settings.EVAL_DIR / 'gold_enrich.csv'}")
        return 0
    if a.cmd == "gold-score":
        from pipeline.gold import score_gold
        scores = score_gold(settings.EVAL_DIR / "gold_enrich.csv")
        (settings.EVAL_DIR / "gold_scores.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")
        print(json.dumps(scores, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_cli.py tests/test_db_load.py -v`
Expected: all passed (DB 필요)

- [ ] **Step 6: Commit**

```bash
git add pipeline/report.py pipeline/__main__.py tests/test_cli.py tests/test_db_load.py
git commit -m "feat(cli): python -m pipeline run/query 와 품질 리포트"
```

---

### Task 12: 태깅 정답셋 샘플링·라벨링·채점

정답 라벨은 enrich 모델(로컬 qwen)과 **다른 모델**(NVIDIA `judge` 작업, 폴백 없음)이 붙이는 실버 라벨이다. 사람이 나중에 CSV의 `gold_*`를 고치면 그 값이 우선한다(이미 채워진 행은 라벨러가 건드리지 않는다).

**Files:**
- Create: `pipeline/gold.py`, `tests/test_gold.py`
- Modify: `pipeline/__main__.py` (`gold-label` 명령 추가)

**Interfaces:**
- Consumes: `read_jsonl`, `CoffeeRecord`, `ReviewRecord`, `coffee_texts`
- Produces:
  - `GOLD_COLUMNS: list[str]`
  - `sample_gold(enriched_dir: Path, norm_dir: Path, out_path: Path, n: int = 50, seed: int = 42) -> int` — 리뷰 텍스트가 있는 원두만, 디카페인 최대 10건 우선 포함, 기존 파일이 있으면 `FileExistsError`(라벨 보호), CSV는 `utf-8-sig`(엑셀 호환)
  - `score_gold(path: Path) -> dict` — `{"acidity"|"body"|"sweetness": {"n", "exact", "within1"}, "is_decaf": {"n", "accuracy"}, "tags": {"n", "jaccard"}}`
  - `GoldLabel(EnrichOutput)` + `is_decaf: bool = False`
  - `label_gold(path: Path, client, vocab: list[str]) -> int` — `gold_*`가 전부 빈 행만 라벨링, 태그는 vocab으로 필터, `LLMError` 행은 건너뜀, 라벨링한 행 수 반환
  - CLI `gold-label` — `label_gold(EVAL_DIR/"gold_enrich.csv", client_for("judge"), tag_vocab(taxonomy))`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_gold.py`

```python
import csv

import pytest

from pipeline.gold import GOLD_COLUMNS, sample_gold, score_gold
from pipeline.records import CoffeeRecord, ReviewRecord, write_jsonl


def setup(tmp_path, n_decaf=3, n_regular=20):
    coffees, reviews = [], []
    for i in range(n_decaf + n_regular):
        key = f"c{i}"
        coffees.append(CoffeeRecord(key=key, name=key, is_decaf=i < n_decaf, acidity=3, flavor_tags=["lemon"],
                                    source="t", collected_at="x"))
        reviews.append(ReviewRecord(key=f"r{i}", coffee_key=key, text=f"text {i}", source="t", collected_at="x"))
    coffees.append(CoffeeRecord(key="notext", name="n", source="t", collected_at="x"))
    write_jsonl(tmp_path / "e" / "coffees.jsonl", coffees)
    write_jsonl(tmp_path / "n" / "reviews.jsonl", reviews)


def test_sample_gold_includes_decaf_and_protects_labels(tmp_path):
    setup(tmp_path)
    out = tmp_path / "gold.csv"
    assert sample_gold(tmp_path / "e", tmp_path / "n", out, n=10) == 10
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert list(rows[0].keys()) == GOLD_COLUMNS
    assert sum(r["pred_is_decaf"] == "1" for r in rows) == 3
    assert all(r["key"] != "notext" and r["gold_acidity"] == "" for r in rows)
    with pytest.raises(FileExistsError):
        sample_gold(tmp_path / "e", tmp_path / "n", out, n=10)


def test_score_gold(tmp_path):
    p = tmp_path / "g.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        base = {c: "" for c in GOLD_COLUMNS}
        w.writerow({**base, "key": "a", "pred_acidity": "4", "gold_acidity": "4", "pred_body": "2", "gold_body": "4",
                    "pred_is_decaf": "1", "gold_is_decaf": "1", "pred_tags": "lemon; honey", "gold_tags": "lemon"})
        w.writerow({**base, "key": "b", "pred_acidity": "", "gold_acidity": "3", "pred_body": "3", "gold_body": "3",
                    "pred_is_decaf": "0", "gold_is_decaf": "1", "pred_tags": "", "gold_tags": ""})
    s = score_gold(p)
    assert s["acidity"] == {"n": 2, "exact": 0.5, "within1": 0.5}
    assert s["body"] == {"n": 2, "exact": 0.5, "within1": 0.5}
    assert s["sweetness"] == {"n": 0, "exact": None, "within1": None}
    assert s["is_decaf"] == {"n": 2, "accuracy": 0.5}
    assert s["tags"] == {"n": 1, "jaccard": 0.5}


class FakeJudge:
    last_model = "judge"

    def __init__(self):
        self.calls = 0

    def chat_json(self, messages, schema):
        self.calls += 1
        if "FAIL" in messages[-1]["content"]:
            from pipeline.llm import LLMError
            raise LLMError("x")
        return schema(flavor_tags=["Lemon", "made-up"], acidity=5, body=None, sweetness=2, is_decaf=True)


def test_label_gold_fills_only_empty_rows(tmp_path):
    p = tmp_path / "g.csv"
    base = {c: "" for c in GOLD_COLUMNS}
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerow({**base, "key": "a", "name": "A", "text": "bright lemon"})
        w.writerow({**base, "key": "b", "name": "B", "text": "x", "gold_acidity": "1"})   # human label kept
        w.writerow({**base, "key": "c", "name": "C", "text": "FAIL"})
    judge = FakeJudge()
    assert label_gold(p, judge, ["lemon", "honey"]) == 1
    rows = {r["key"]: r for r in csv.DictReader(p.open(encoding="utf-8-sig"))}
    assert (rows["a"]["gold_acidity"], rows["a"]["gold_body"], rows["a"]["gold_sweetness"]) == ("5", "", "2")
    assert (rows["a"]["gold_is_decaf"], rows["a"]["gold_tags"]) == ("1", "lemon")
    assert rows["b"]["gold_acidity"] == "1" and rows["b"]["gold_body"] == ""
    assert rows["c"]["gold_acidity"] == ""
    assert judge.calls == 2
```

`tests/test_gold.py` 맨 위 import를 다음으로 한다:
```python
import csv

import pytest

from pipeline.gold import GOLD_COLUMNS, label_gold, sample_gold, score_gold
from pipeline.records import CoffeeRecord, ReviewRecord, write_jsonl
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_gold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.gold'`

- [ ] **Step 3: 구현** — `pipeline/gold.py`

```python
import csv
import random
from pathlib import Path

from pipeline.enrich import coffee_texts
from pipeline.records import CoffeeRecord, ReviewRecord, read_jsonl

SCORES = ("acidity", "body", "sweetness")
GOLD_COLUMNS = (["key", "name", "text"] + [f"pred_{s}" for s in SCORES] + ["pred_is_decaf", "pred_tags"]
                + [f"gold_{s}" for s in SCORES] + ["gold_is_decaf", "gold_tags"])
MAX_DECAF = 10


def sample_gold(enriched_dir: Path, norm_dir: Path, out_path: Path, n: int = 50, seed: int = 42) -> int:
    if out_path.exists():
        raise FileExistsError(f"{out_path} exists; move it away before resampling (it may hold labels)")
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    pool = [c for c in read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord) if texts.get(c.key)]
    rng = random.Random(seed)
    decaf = [c for c in pool if c.is_decaf]
    picked = rng.sample(decaf, min(MAX_DECAF, len(decaf), n))
    rest = [c for c in pool if not c.is_decaf]
    picked += rng.sample(rest, min(n - len(picked), len(rest)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for c in picked:
            row = {col: "" for col in GOLD_COLUMNS}
            row.update(key=c.key, name=c.name, text=texts[c.key][:2000],
                       pred_is_decaf="1" if c.is_decaf else "0", pred_tags="; ".join(c.flavor_tags),
                       **{f"pred_{s}": "" if getattr(c, s) is None else str(getattr(c, s)) for s in SCORES})
            w.writerow(row)
    return len(picked)


def _tags(s: str) -> set[str]:
    return {t.strip().lower() for t in s.split(";") if t.strip()}


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def score_gold(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    out: dict = {}
    for s in SCORES:
        pairs = [(r[f"pred_{s}"], int(r[f"gold_{s}"])) for r in rows if r[f"gold_{s}"].strip()]
        exact = [1.0 if p.strip() and int(p) == g else 0.0 for p, g in pairs]
        within = [1.0 if p.strip() and abs(int(p) - g) <= 1 else 0.0 for p, g in pairs]
        out[s] = {"n": len(pairs), "exact": _mean(exact), "within1": _mean(within)}
    dec = [(r["pred_is_decaf"].strip() == "1", r["gold_is_decaf"].strip() in ("1", "true", "yes", "y"))
           for r in rows if r["gold_is_decaf"].strip()]
    out["is_decaf"] = {"n": len(dec), "accuracy": _mean([1.0 if p == g else 0.0 for p, g in dec])}
    jac = []
    for r in rows:
        gold = _tags(r["gold_tags"])
        if gold:
            pred = _tags(r["pred_tags"])
            jac.append(len(pred & gold) / len(pred | gold))
    out["tags"] = {"n": len(jac), "jaccard": _mean(jac)}
    return out


class GoldLabel(EnrichOutput):
    is_decaf: bool = False


LABEL_SYSTEM = "You are an expert coffee cupper labelling an evaluation set. Answer from the tasting text only. Reply with one JSON object."
LABEL_PROMPT = """Coffee: {name}
Tasting text:
{text}

Allowed flavor tags: {vocab}

Return JSON: {{"flavor_tags": [up to 6 allowed tags], "acidity": 1-5 or null, "body": 1-5 or null, "sweetness": 1-5 or null, "is_decaf": true or false}}
Scale: 1 = very low, 3 = moderate, 5 = very high. Use null when the text gives no evidence."""
GOLD_FIELDS = [f"gold_{s}" for s in SCORES] + ["gold_is_decaf", "gold_tags"]


def label_gold(path: Path, client, vocab: list[str]) -> int:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    vocab_set = set(vocab)
    labelled = 0
    for r in rows:
        if any(r[f].strip() for f in GOLD_FIELDS):
            continue  # a human (or an earlier run) already labelled this row
        messages = [{"role": "system", "content": LABEL_SYSTEM},
                    {"role": "user", "content": LABEL_PROMPT.format(name=r["name"], text=r["text"], vocab=", ".join(vocab))}]
        try:
            o = client.chat_json(messages, GoldLabel)
        except LLMError:
            continue
        for s in SCORES:
            r[f"gold_{s}"] = "" if getattr(o, s) is None else str(getattr(o, s))
        r["gold_is_decaf"] = "1" if o.is_decaf else "0"
        r["gold_tags"] = "; ".join(t.lower() for t in o.flavor_tags if t.lower() in vocab_set)
        labelled += 1
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return labelled
```

`pipeline/gold.py` 맨 위 import에 다음을 추가한다:
```python
from pipeline.enrich import EnrichOutput, coffee_texts
from pipeline.llm import LLMError
```
(기존 `from pipeline.enrich import coffee_texts` 줄을 위 줄로 바꾼다.)

- [ ] **Step 3b: CLI에 `gold-label` 추가** — `pipeline/__main__.py`

`build_parser()`에서 `sub.add_parser("gold-score", ...)` 줄 바로 앞에:
```python
    sub.add_parser("gold-label", help="fill empty gold labels with the judge model")
```
`main()`에서 `if a.cmd == "gold-score":` 블록 바로 앞에:
```python
    if a.cmd == "gold-label":
        from pipeline.enrich import tag_vocab
        from pipeline.gold import label_gold
        from pipeline.llm import client_for
        from pipeline.records import TaxonomyNode, read_jsonl

        vocab = tag_vocab(read_jsonl(settings.NORMALIZED_DIR / "taxonomy.jsonl", TaxonomyNode))
        n = label_gold(settings.EVAL_DIR / "gold_enrich.csv", client_for("judge"), vocab)
        print(f"labelled {n} rows")
        return 0
```
`tests/test_cli.py` 끝에:
```python
def test_gold_label_command_exists():
    assert build_parser().parse_args(["gold-label"]).cmd == "gold-label"
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_gold.py -v`
Expected: all passed

- [ ] **Step 5: 전체 테스트**

Run: `uv run pytest -v`
Expected: all passed (DB 테스트 포함 — Docker DB 실행 중이어야 함)

- [ ] **Step 6: Commit**

```bash
git add pipeline/gold.py pipeline/__main__.py tests/test_gold.py tests/test_cli.py
git commit -m "feat(eval): 태깅 정답셋 샘플링·judge 모델 실버 라벨링·필드별 일치율 채점"
```

---

### Task 13: 실데이터 실행 + 정답셋 라벨링 + README

실제 네트워크·모델·DB를 쓰는 작업이라 테스트 대신 **각 단계 출력을 확인**한다. 스펙 3.6 완료 기준을 여기서 충족한다.

**Files:**
- Create: `data/eval/gold_enrich.csv`, `data/eval/gold_scores.json`
- Modify: `README.md` (전면 재작성)

- [ ] **Step 1: DB 기동 확인**

Run: `docker compose up -d db && docker compose ps`
Expected: `db` 서비스 `healthy`. Docker Desktop이 꺼져 있으면 사용자에게 켜달라고 요청하고 멈춘다.

- [ ] **Step 2: 수집**

Run: `uv run python -m pipeline run --only collect`
Expected: `[collect]` 줄에 8개 소스가 모두 `"ok"`. 실패 소스가 있으면 `data/raw/<source>/<date>/manifest.json`의 `error`를 확인해 원인을 사용자에게 보고한다(재시도는 `--source <name>`).

- [ ] **Step 3: 정규화**

Run: `uv run python -m pipeline run --only normalize`
Expected: `coffees`가 대략 9,000 이상(coffeereview 약 7,500 + CQI 약 1,546 + RoasterDB 100 + Shopify 수 건), `menu_items` 수백, `brands` 10, `taxonomy` 약 120. 숫자가 크게 다르면 소스별 `src:*` 값을 보고 원인을 찾는다.

- [ ] **Step 4: enrich 스모크 (20건)**

Run: `uv run python -m pipeline run --only enrich --limit 20`
Expected: `llm_calls: 20`, `llm_failed`가 20건 중 2건 이하. `data/enriched/cache.jsonl` 마지막 줄들의 `output`이 그럴듯한지 눈으로 확인한다.

- [ ] **Step 5: enrich 전체 (백그라운드, 재개 가능)**

Run: `uv run python -m pipeline run --only enrich` (Bash `run_in_background`로 실행)
Expected: 완료 시 `llm_failed` 비율 5% 이하. 중간에 끊겨도 같은 명령으로 이어서 진행된다. 실패가 많으면 `--retry-failed`로 한 번 더 돌린다.

- [ ] **Step 6: 임베딩 + 적재 + 리포트**

Run: `uv run python -m pipeline run --only embed --only load`
Expected: 리포트 마크다운 출력, `data/reports/quality_<date>.md` 생성, `embedding` 결측률 0%.

- [ ] **Step 7: 유사도 질의 확인 (완료 기준 4)**

Run:
```bash
uv run python -m pipeline query "bright citrus floral Ethiopia washed" -k 5
uv run python -m pipeline query "bright citrus floral Ethiopia washed" -k 5 --decaf
```
Expected: 첫 결과들이 에티오피아/산미 높은 원두, 두 번째는 전부 `decaf=True`.

- [ ] **Step 8: 정답셋 샘플 + 실버 라벨링 + 채점 (완료 기준 3)**

Run:
```bash
uv run python -m pipeline gold-sample --n 50
uv run python -m pipeline gold-label
uv run python -m pipeline gold-score
```
Expected: `labelled` 45 이상(NVIDIA 타임아웃 행은 비어 있을 수 있음 — `gold-label`을 한 번 더 돌리면 빈 행만 다시 시도), `data/eval/gold_scores.json` 생성, 필드별 `exact`/`within1`/`accuracy`/`jaccard` 수치. 사용자에게 라벨링을 요청하지 않는다.

- [ ] **Step 9: README 재작성**

`README.md`를 아래 구조로 다시 쓴다. 숫자는 `data/reports/quality_<date>.md`와 `data/eval/gold_scores.json`에서 그대로 옮긴다.

```markdown
# ☕ Coffee Sommelier — 내 커피 취향을 알아주는 앱

> 건강 때문에 디카페인을 마시지만 산미 있는 커피를 좋아하는 사람도, 카페에서 실패 없이 고를 수 있게.

## 지금 단계: 1단계 데이터 기반 (완료)
- 지식베이스: 원두 N건(소스별 표), 리뷰 N건, 프랜차이즈 메뉴 N건(카페인 mg 포함), 브랜드 10곳, SCA 향미 택소노미
- 파이프라인: collect → normalize → enrich(규칙 우선 + 로컬 LLM) → embed(bge-m3) → load(pgvector)
- 품질: 필드별 결측률 표, 태깅 정답셋 50건 대비 일치율 표 (정답 라벨 = enrich와 다른 모델 deepseek-v4.1-flash의 실버 라벨, 사람 검수 시 CSV에서 덮어쓰기)

## 빠른 시작
docker compose up -d db
uv sync
uv run python -m pipeline run
uv run python -m pipeline query "bright citrus floral" --decaf

## 설계 결정
- 카페인은 절대 조건(필터), 산미·바디는 취향 점수
- 데이터가 적은 도메인: 소스 내 5분위 점수화, 규칙 우선 + 빈 칸만 LLM
- 모델: 로컬 Ollama(대량 배치) + NVIDIA 엔드포인트(실시간), 타임아웃·폴백

## 데이터 출처
(소스별 URL과 라이선스 표 — 스펙 3.1 표를 그대로 옮기고, coffeereview 스크랩 데이터는 Kaggle 경유이며 원 저작권은 Coffee Review에 있음을 명시)

## 로드맵
2단계 추천+기록 → 3단계 스캔+추론+평가 → 4단계 에이전트

## 와인 v1에서 배운 점
(태그 `wine-v1`) 필터가 프롬프트 텍스트로만 전달되던 문제, 평가 체계 부재, 태그 12개라고 적었지만 실제 2개였던 문서-코드 불일치 → 이번엔 리포트와 정답셋으로 수치를 먼저 만든다.
```

- [ ] **Step 10: Commit**

```bash
git add README.md data/eval/gold_enrich.csv data/eval/gold_scores.json
git commit -m "docs: 커피 소믈리에 README, 1단계 품질 리포트·정답셋 결과"
```
