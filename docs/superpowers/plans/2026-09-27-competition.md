# 공모전 제출본 — 오픈 데이터판 배포·CSV·이미지·초안 완성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** coffeereview가 없는 DB로 도는 별도 배포(심사위원용 URL), 그 DB에서 잰 수치, 포털 업로드용 CSV 3종, 결과 이미지 5장, 300자 검사까지 통과한 초안.

**Architecture:** 파이프라인에 소스 제외 옵션 하나(`--exclude-source`), 설정 디렉터리의 env 오버라이드, `/health`의 `variant` 필드. 배포는 기존 Dockerfile·render.yaml·deploy_all.sh를 "variant"로 한 벌 더 돌리는 것. CSV·이미지·검사는 `scripts/competition/` 아래 독립 스크립트.

**Tech Stack:** Python 3.12, uv, pytest, Postgres/pgvector(docker), Neon, Render, Vercel, matplotlib(dev 의존성으로 추가), Next.js 16.

**Spec:** `docs/superpowers/specs/2026-09-27-competition-design.md`

## Global Constraints

- 제출본 DB에 `coffeereview_kaggle` 소스가 한 행도 없어야 한다(`SELECT count(*) FROM coffees WHERE source='coffeereview_kaggle'` = 0, reviews 0).
- CSV에는 리뷰·설명 원문 열(`flavor_summary`, review text)을 넣지 않는다. RoasterDB·coffeereview·SCA 휠 원문 파생 행은 넣지 않는다. UTF-8 BOM.
- 초안 수치는 `data/eval/open/*.json`에서 스크립트로 생성해 붙인다(손으로 적지 않는다).
- 전체판 파일(`data/normalized/`, `data/eval/phase2_*.json`, dev DB `coffee`)을 덮어쓰지 않는다 — 제출본은 항상 별도 경로·별도 DB.
- 커밋 메시지 한국어 conventional + 세션 트레일러. `uv run pytest -q` 전부 통과 후 커밋.

## Review Focus

- **제출본 DB에 coffeereview가 남음**(정규화 제외를 빠뜨리거나 캐시 경로로 들어옴) → Task 2의 count 검사 + Task 1 테스트.
- **전체판 정규화 결과가 덮어써짐** → `NORMALIZED_DIR` 오버라이드 테스트(Task 1).
- **CSV에 원문 텍스트 유출** → Task 4 테스트(금지 열 없음, 금지 소스 없음).
- **배너가 전체판에도 보임** → Task 3 웹 테스트(env 없으면 렌더 안 함).
- **초안의 숫자와 JSON 불일치** → Task 6은 생성된 조각을 붙이고 `check_draft.py`가 조각 마커를 검사.

## File Structure

```
pipeline/settings.py                     Task 1: NORMALIZED_DIR/EVAL_DIR/EXCLUDE_SOURCES env
pipeline/normalize/__init__.py, __main__.py   Task 1: --exclude-source
app/api.py, app/config.py                Task 1: /health variant + coffees
scripts/deploy/migrate_to_neon.sh, deploy_all.sh, render.yaml, .github/workflows/ci.yml   Task 3
web/components/VariantBanner.tsx, web/app/layout.tsx, web/tests/variantbanner.test.tsx   Task 3
scripts/competition/export_csv.py, figures.py, check_draft.py, render_numbers.py   Task 4·5·6
data/competition/*.csv, docs/competition/images/*.png, data/eval/open/*.json
docs/competition/data-recipe-draft.md    Task 6
```

---

### Task 1: 소스 제외 옵션, 디렉터리 오버라이드, `/health` variant

**Files:**
- Modify: `pipeline/settings.py`, `pipeline/normalize/__init__.py` (`run_normalize(..., exclude_sources=())`), `pipeline/__main__.py` (`run --exclude-source X` 반복 가능; env `EXCLUDE_SOURCES` 쉼표 구분), `app/config.py` (`DATA_VARIANT = os.getenv("DATA_VARIANT", "full")`), `app/api.py` (`/health` → `{"ok": true, "variant": ..., "coffees": repo.count_coffees()}`), `app/repo.py` (`count_coffees()`), `tests/app/fakes.py`(FakeRepo.count_coffees)
- Test: `tests/test_settings.py`, `tests/test_normalize_menus.py`(run_normalize 제외), `tests/test_cli.py`, `tests/app/test_api.py`

**Interfaces:**
- Produces: `settings.NORMALIZED_DIR`·`settings.EVAL_DIR`가 env `NORMALIZED_DIR`/`EVAL_DIR`로 바뀐다(절대경로). `run_normalize(raw_root, out_dir, curated_dir, exclude_sources: tuple[str, ...] = ())` — 제외 소스는 `per_source["src:<name>"] = "excluded"`. CLI `--exclude-source NAME`(action=append) 또는 env `EXCLUDE_SOURCES=a,b`. `GET /health` → `{"ok": True, "variant": "<DATA_VARIANT>", "coffees": <int>}`.

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/test_settings.py
def test_dir_overrides_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NORMALIZED_DIR", str(tmp_path / "n")); monkeypatch.setenv("EVAL_DIR", str(tmp_path / "e"))
    import importlib; from pipeline import settings; importlib.reload(settings)
    assert settings.NORMALIZED_DIR == tmp_path / "n" and settings.EVAL_DIR == tmp_path / "e"
    monkeypatch.delenv("NORMALIZED_DIR"); monkeypatch.delenv("EVAL_DIR"); importlib.reload(settings)

# tests/test_normalize_menus.py
def test_run_normalize_excludes_sources(tmp_path, monkeypatch):
    # arrange a raw root with a coffeereview_kaggle snapshot AND a cqi snapshot (reuse existing fixture helpers/inline data)
    ...
    stats = run_normalize(raw, out, curated, exclude_sources=("coffeereview_kaggle",))
    assert stats["src:coffeereview_kaggle"] == "excluded"
    coffees = read_jsonl(out / "coffees.jsonl", CoffeeRecord)
    assert coffees and not any(c.source == "coffeereview_kaggle" for c in coffees)
    assert not any(r.source == "coffeereview_kaggle" for r in read_jsonl(out / "reviews.jsonl", ReviewRecord))

# tests/test_cli.py
def test_exclude_source_flag_and_env(monkeypatch):
    from pipeline.__main__ import build_parser, exclude_sources_from
    a = build_parser().parse_args(["run", "--only", "normalize", "--exclude-source", "coffeereview_kaggle"])
    assert exclude_sources_from(a) == ("coffeereview_kaggle",)
    monkeypatch.setenv("EXCLUDE_SOURCES", "a, b")
    assert exclude_sources_from(build_parser().parse_args(["run"])) == ("a", "b")

# tests/app/test_api.py
def test_health_reports_variant_and_count(client, monkeypatch):
    monkeypatch.setattr("app.config.DATA_VARIANT", "open")
    r = client.get("/health").json()
    assert r["ok"] is True and r["variant"] == "open" and isinstance(r["coffees"], int)
```
(파서 빌더 함수 이름은 `pipeline/__main__.py`의 실제 이름을 따른다; 없으면 `build_parser()`로 추출.)

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현** — settings: `NORMALIZED_DIR = Path(os.getenv("NORMALIZED_DIR") or DATA_DIR / "normalized")` 식으로 두 상수 교체. `run_normalize`: `for name, fn in _normalizers().items(): if name in exclude_sources: per_source[f"src:{name}"] = "excluded"; continue`. `__main__`: 인자·env 합치는 `exclude_sources_from(a)`, `run_normalize(..., exclude_sources=...)`. `/health`: `repo.count_coffees()`는 `SELECT count(*) FROM coffees WHERE active`; `app.config.DATA_VARIANT`를 요청 시점에 읽는다(monkeypatch 가능하게 모듈 속성 참조).
- [ ] **Step 4: 통과 + 커밋** — `git commit -m "feat(pipeline): 소스 제외 옵션·디렉터리 오버라이드, /health에 데이터 변형·원두 수"`

---

### Task 2: 로컬 오픈 데이터판 DB 구축 + 평가

**Files:**
- Create: `scripts/competition/build_open_db.sh`, `data/eval/open/*.json`(violations, loo, coverage, convergence, bench, explain_quality, decaf probe는 compare3 대신 `coverage`+`loo`만 — compare3는 전체판 전용)
- Modify: `docs/competition/checklist.md`(해당 항목 체크)

**Interfaces:**
- Produces: 스크립트가 (1) docker 컨테이너 `hawksbill-db-1`에 DB `coffee_open` 생성(있으면 `RESET=1`일 때만 재생성), (2) `NORMALIZED_DIR=data/normalized_open EXCLUDE_SOURCES=coffeereview_kaggle uv run python -m pipeline run --only normalize,load` with `DATABASE_URL=postgresql://coffee:coffee@localhost:5432/coffee_open`(enrich/embed 캐시는 기본 경로 그대로 읽힘 — `run_load`가 embedded_dir에서 키로 찾는지 확인; 없는 키는 embed 단계 필요 → 이 경우 `--only normalize,embed,load`로 돌리되 캐시 히트라 새 요청 0이어야 함, 로그로 확인), (3) 검증 쿼리 출력(coffees 총수, source별, coffeereview 0, menu_items 471), (4) `EVAL_DIR=data/eval/open DATABASE_URL=... uv run python -m app.eval violations loo coverage convergence bench explain_quality`.

- [ ] **Step 1: 스크립트 작성·실행** — 위 순서. `data/normalized_open/`·`data/eval/open/` 경로를 `.gitignore`에서 확인(`data/normalized*`는 무시, `data/eval/`은 커밋 대상).
- [ ] **Step 2: 검증** — coffeereview 0, coffees ≈ 1,762(±20), menu_items 471, `violations == 0`. 수치를 보고서에 기록.
- [ ] **Step 3: 커밋** — `git add scripts/competition/build_open_db.sh data/eval/open docs/competition/checklist.md && git commit -m "feat(competition): 오픈 데이터판 DB 구축 스크립트와 제출본 평가 결과"`

---

### Task 3: 제출본 배포(Neon `coffee_open` · Render 두 번째 서비스 · Vercel 두 번째 프로젝트 · 배너)

**Files:**
- Modify: `scripts/deploy/migrate_to_neon.sh`(`TARGET_DB` 지정 가능: 연결 문자열의 DB명을 바꾸는 대신 `NEON_DATABASE_URL`을 그대로 받되 `SRC_DB` env로 원본 DB 선택 — 이미 지원; 문서만), `scripts/deploy/deploy_all.sh`(`VARIANT=open` → `NAME=coffee-sommelier-open`, `RENDER_SERVICE=coffee-sommelier-open-api`, Neon DB `coffee_open` 생성(`neonctl databases create --project-id … --name coffee_open`) 후 `SRC_DB=coffee_open` 마이그레이션, Render env `DATA_VARIANT=open`, Vercel env `NEXT_PUBLIC_VARIANT=open`), `render.yaml`(두 번째 서비스 블록, `DATA_VARIANT: open`), `.github/workflows/ci.yml`(deploy job이 두 서비스 순회), `web/app/layout.tsx`(배너 삽입)
- Create: `web/components/VariantBanner.tsx`, `web/tests/variantbanner.test.tsx`, `docs/deploy.md` "제출본" 절

**Interfaces:**
- Produces: `<VariantBanner />` — `process.env.NEXT_PUBLIC_VARIANT === "open"`일 때만 `<p role="note">공모전 제출본 · 오픈 데이터 + 국내 로스터리 사실정보 (coffeereview 미포함)</p>` 렌더, 아니면 null. `deploy_all.sh VARIANT=open` 경로.

- [ ] **Step 1: 실패하는 테스트** — `web/tests/variantbanner.test.tsx`: env 없음 → `queryByRole("note")` null; `vi.stubEnv("NEXT_PUBLIC_VARIANT","open")` + 동적 import → 텍스트 존재.
- [ ] **Step 2: 구현** — 위 파일들. `render.yaml` 두 번째 서비스는 첫 번째와 동일 env + `DATA_VARIANT=open`, `DATABASE_URL`(sync:false). CI deploy job: 서비스 이름 목록 `for name in coffee-sommelier-api coffee-sommelier-open-api` (없는 서비스는 건너뜀).
- [ ] **Step 3: 실제 배포는 컨트롤러가 수행**(자격 증명 보유): `VARIANT=open bash scripts/deploy/deploy_all.sh` 실행 → `https://<open web>/api/health`가 `variant: open`, `coffees ≈ 1,762` 반환. 결과 URL을 `docs/competition/checklist.md`와 초안 A-0에 기록.
- [ ] **Step 4: 커밋** — `git commit -m "feat(deploy): 공모전 제출본(오픈 데이터판) 별도 배포 경로와 화면 배너"`

---

### Task 4: CSV 내보내기

**Files:**
- Create: `scripts/competition/export_csv.py`, `tests/test_export_csv.py`, `data/competition/*.csv`

**Interfaces:**
- Produces: `export_csv.main(database_url, out_dir)` → 파일: `01_컬럼정의.csv`(변수번호, 타깃여부, 타입, 컬럼명, 비고 — coffees/menu_items/brands 순), `02_데이터_템플릿.csv`(테이블명 + 각 3행), `03_데이터셋_coffees.csv`(source ∈ {cqi, roasters_kr, shopify}; 열: key,name,roaster,origin_country,origin_region,process,roast_level,is_decaf,decaf_process,acidity,body,sweetness,flavor_tags,source,source_url,collected_at), `04_데이터셋_menu_items.csv`(brand_key,name,name_en,category,is_decaf,decaf_option,caffeine_mg,source_url,collected_at), `05_데이터셋_brands.csv`, `06_데이터셋_milk_labels.csv`(name,is_milk), `07_데이터셋_sca_ko.csv`(key,name_en,name_ko,level). 순수 함수 `rows_for_coffees(rows) -> list[dict]`(금지 열 제거·소스 필터).

- [ ] **Step 1: 실패하는 테스트** — `rows_for_coffees`가 `flavor_summary`를 떨어뜨리고 `roasterdb`/`coffeereview_kaggle` 행을 제외; 파일 헤더 첫 바이트가 BOM; 컬럼정의 행 수 = 세 테이블 열 수 합.
- [ ] **Step 2: 구현 + 실행**(오픈 DB) — `uv run python scripts/competition/export_csv.py --db postgresql://coffee:coffee@localhost:5432/coffee_open`. 파일 크기와 행 수 보고.
- [ ] **Step 3: 커밋** — `git commit -m "feat(competition): 포털 업로드용 CSV 내보내기(원문 텍스트·제한 소스 제외)"`

---

### Task 5: 결과 이미지 5장 + 초안 검사 스크립트

**Files:**
- Create: `scripts/competition/figures.py`, `scripts/competition/check_draft.py`, `scripts/competition/render_numbers.py`, `docs/competition/images/01..05.png`, `tests/test_competition_scripts.py`
- Modify: `pyproject.toml`(dev 의존성 `matplotlib`)

**Interfaces:**
- Produces: `figures.main(eval_dir: Path, out_dir: Path)` 5장(파일명 스펙 §5). `render_numbers.main(eval_dir) -> str`(초안에 붙일 마크다운 조각: 커버리지 표, 위반, LOO, 수렴, 벤치, 설명 품질 — `<!-- numbers:start -->`/`<!-- numbers:end -->` 마커 사이 내용). `check_draft.main(path) -> int`(A-2~A-6 각 300자(공백 제외) 이상, 【작성 필요】 개수 출력, numbers 마커 존재; 실패 시 1).

- [ ] **Step 1: 테스트** — 작은 가짜 eval JSON으로 `figures.main`이 5개 png를 만들고, `render_numbers.main`이 위반 수와 원두 수를 포함한 문자열을 내며, `check_draft`가 299자 항목에서 1을 반환.
- [ ] **Step 2: 구현 + 실행**(`data/eval/open`) — 한글 폰트: `matplotlib.font_manager`로 `Malgun Gothic` 있으면 사용, 없으면 영문 라벨. 이미지를 직접 열어(Read) 겹침·깨진 글자 확인.
- [ ] **Step 3: 커밋** — `git commit -m "feat(competition): 결과 이미지 5장 생성, 수치 조각·초안 검사 스크립트"`

---

### Task 6: 초안 갱신

**Files:**
- Modify: `docs/competition/data-recipe-draft.md`, `docs/competition/checklist.md`

- [ ] **Step 1**: 초안에 `<!-- numbers:start -->…<!-- numbers:end -->` 마커를 넣고 `render_numbers.py` 출력으로 채운다(표·수치 전부). 본문 문장 속 수치(예: "0/78")를 마커 밖에 남기지 않는다 — 문장은 "아래 표 참조" 식으로 바꾸거나 스크립트 출력에 포함.
- [ ] **Step 2**: B-6에 이미지 5장(`docs/competition/images/…`)과 해석 문단 각 3~5문장(수치는 JSON 값). 생성형 AI 고지 문단 실제 범위로(Claude Code: 코드·문서·데이터 라벨 보조; qwen3.5 9B: 원두 속성 구조화; nemotron-3-super: 설명 생성·후기 파싱; deepseek-v4.1-flash·nemotron: 태깅·설명 품질 판정; nemotron-3-embed-1b: 임베딩).
- [ ] **Step 3**: A-0에 제출본 URL(Task 3 결과), 전체판 URL은 "참고"로. `uv run python scripts/competition/check_draft.py docs/competition/data-recipe-draft.md` → 통과(남은 【작성 필요】는 개인정보·드롭다운·전화확인·팀 구성만; 개수를 checklist에 적는다).
- [ ] **Step 4: 커밋** — `git commit -m "docs(competition): 제출본 수치·이미지·AI 고지로 초안 갱신"`
