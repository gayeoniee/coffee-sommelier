# 실사용 품질 — 빠른 설명 + 5개 브랜드 메뉴 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영 설명 폴백을 거의 0으로 만들고(생각 끄기), 할리스·커피빈·이디야·폴바셋·컴포즈의 실제 커피 메뉴(이름·카페인·디카페인)를 기존 파이프라인으로 적재해 운영에 반영한다.

**Architecture:** 설명은 `config/models.yaml` 한 줄. 메뉴는 브랜드별 `Collector`(`pipeline/collect/web.py`) + `normalize_<brand>`(`pipeline/normalize/menus.py`) 쌍을 기존 스타벅스·메가·빽다방과 같은 모양으로 추가하고, 디카페인 옵션 규칙은 `brands.yaml`의 새 필드로 데이터화한다. 앱 코드는 바뀌지 않는다.

**Tech Stack:** Python 3.12, httpx + `PoliteClient`(robots·지연), BeautifulSoup/lxml, pydantic records, pytest(HTML 픽스처), Postgres/pgvector, NVIDIA API.

**Spec:** `docs/superpowers/specs/2026-09-27-quality-menus-design.md`

## Global Constraints

- 수집은 반드시 `PoliteClient`로(robots.txt 재확인, 호스트당 1초 이상 지연). 브랜드당 페이지 수는 커피 카테고리에 필요한 만큼만. 원본은 `data/raw/<brand>/<YYYY-MM-DD>/`에 저장(`data/raw`는 git 무시).
- 커피 음료 카테고리만 수집·정규화(티·에이드·스무디·푸드 제외). 카테고리 단위로 거르고 이름으로 거르지 않는다.
- `caffeine_mg`: HOT/ICED 둘 다 있으면 큰 값, 사이즈는 기본(레귤러). 숫자는 `pipeline.rules.num`로 파싱.
- 파서 테스트는 네트워크 없이 `tests/fixtures/menus/<brand>/`의 **트림한** 픽스처(필요한 마크업만, 각 파일 30KB 이하)로 돈다. 픽스처 저장 시 개인정보·스크립트는 제거.
- 새 `MenuItemRecord.key`는 `menu:<brand>:<사이트 id>`(id가 없으면 이름). 같은 이름이 HOT/ICED로 두 항목이면 하나로 합친다(큰 카페인).
- 커밋 메시지는 한국어 conventional(`feat(collect): …`), 세션 트레일러 유지. `uv run pytest -q` 전부 통과 후 커밋.

## Review Focus

- **디카페인만 손님 + 새 브랜드**: 디카페인 SKU가 없고 샷 변경도 안 되는 카테고리(콜드브루 등)는 카드에 나오면 안 된다 → 위반 평가 0/N(Task 8) + Task 2의 규칙 테스트.
- **`caffeine_rule=low` 손님**: 카페인 수치가 없는 항목은 디카페인 옵션이 없으면 제외되어야 한다(기존 `passes`). 새 브랜드 카페인 채움률 ≥ 90%를 Task 3~7 각각의 테스트가 강제한다.
- **같은 음료의 HOT/ICED 중복 카드** → 정규화가 합친다(Task 3~7 테스트).
- **생각 끄기 후 설명 품질** → 파싱 일치율 측정(Task 1), 운영 카드 눈 검증(Task 8).
- **사이트 구조가 조사와 다름** → 각 브랜드 태스크의 Step 1이 실제 페이지를 먼저 받아 확인하고, 안 되면 사유와 함께 BLOCKED로 보고(다른 브랜드는 계속).

## File Structure

```
config/models.yaml                         Task 1: explain(+parse) extra
docs/adr/0004-explain-thinking.md          Task 1: 측정·결정
data/eval/phase2_thinking.json, phase2_bench.json
pipeline/http.py                           Task 2: PoliteClient(verify=)
pipeline/records.py                        Task 2: BrandRecord.decaf_option_categories
data/curated/brands.yaml                   Task 2: 브랜드별 decaf_option_categories
pipeline/normalize/menus.py                Task 2: menu_decaf_option(); Task 3~7: normalize_<brand>
pipeline/collect/web.py, registry.py       Task 3~7: <Brand>Collector
pipeline/normalize/__init__.py             Task 3~7: 등록
tests/fixtures/menus/<brand>/*.html        Task 3~7
tests/test_normalize_menus.py, tests/test_http.py, tests/test_collect.py
data/curated/menu_milk_labels.yaml         Task 8 (수기)
README.md                                  Task 8
```

---

### Task 1: 설명 모델 생각 끄기 + 파싱 태스크 측정

**Files:**
- Modify: `config/models.yaml` (explain, 측정 결과에 따라 parse_note/parse_bean)
- Create: `docs/adr/0004-explain-thinking.md`, `data/eval/phase2_thinking.json`
- Modify: `data/eval/phase2_bench.json`, `README.md`(벤치 표 한 줄)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `config/models.yaml` `tasks.explain.extra == {"chat_template_kwargs": {"enable_thinking": false}}`; `load_targets("explain")[0].extra`에 그 값이 실려 `_payload`가 요청 본문 최상위에 넣는다(이미 그렇게 동작 — 테스트로 고정).

- [ ] **Step 1: 실패하는 테스트** — `tests/test_llm.py` 끝에:
```python
def test_explain_target_disables_thinking():
    from pipeline.llm import load_targets
    primary, _ = load_targets("explain")
    assert primary.extra == {"chat_template_kwargs": {"enable_thinking": False}}


def test_payload_puts_extra_at_top_level():
    from app.llm import _payload
    from pipeline.llm import Target
    t = Target(provider="nvidia", model="m", base_url="http://x", api_key=None, timeout=1, max_tokens=5,
               extra={"chat_template_kwargs": {"enable_thinking": False}})
    p = _payload(t, [{"role": "user", "content": "hi"}], stream=True)
    assert p["chat_template_kwargs"] == {"enable_thinking": False} and p["stream"] is True
```
(`Target`의 실제 필드명은 `pipeline/llm.py`를 읽고 맞춘다.)

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_llm.py -q` → FAIL (extra 없음)

- [ ] **Step 3: 설정** — `config/models.yaml`의 `explain:` 블록에 `extra: {chat_template_kwargs: {enable_thinking: false}}` 추가(fallback 줄은 그대로).

- [ ] **Step 4: 파싱 태스크 측정** — 스크립트 `.superpowers/thinking_probe.py`(커밋하지 않음)로 `parse_note`·`parse_bean`을 생각 on/off 각각 돌린다. 입력: `app/core/parse.py`의 few-shot 예문 전부 + `tests/app/test_parse_explain.py`의 케이스 + 다음 5개 후기: "산미가 너무 셌어요", "너무 달고 무거웠어요", "고소하고 부드러워서 좋았어요", "디카페인인데도 향이 살아있네요", "물 탄 것처럼 밍밍했어요"; 원두 문구 5개: "에티오피아 예가체프 워시드 디카페인", "콜롬비아 수프리모 미디엄 로스트 초콜릿 견과", "케냐 AA 자몽 블랙커런트", "브라질 내추럴 다크 로스트", "과테말라 안티구아 허니 프로세스 스위스워터 디카페인". 각 입력 3회. 기록: 결과 JSON(방향·태그·is_decaf 등)의 일치 여부(on vs off 다수결), 지연 중앙값. `data/eval/phase2_thinking.json`에 저장.
  판단: off의 일치율 ≥ on의 일치율 - 5%p 이면 parse 두 태스크도 `extra` 추가. 아니면 그대로 두고 ADR에 이유.

- [ ] **Step 5: 벤치 재측정** — `uv run python -m app.eval bench` → `data/eval/phase2_bench.json` 갱신. README의 벤치 문장/표(첫 토큰·병렬 합계)를 새 수치로 교체.

- [ ] **Step 6: ADR** — `docs/adr/0004-explain-thinking.md`: 문제(운영 폴백 3/8, 첫 토큰 5~20초), 원인(모델 기본 thinking), 측정(생각 on/off 첫 토큰·전체·파싱 일치율 표), 결정, 되돌리는 방법.

- [ ] **Step 7: 통과 확인 + 커밋** — `uv run pytest -q` 전부 PASS → `git add config/models.yaml docs/adr/0004-explain-thinking.md data/eval/phase2_thinking.json data/eval/phase2_bench.json README.md tests/test_llm.py && git commit -m "perf(explain): 설명 모델 thinking 끔 — 첫 토큰 10초→0.7초, 파싱 태스크 측정"`

---

### Task 2: 수집·정규화 공통 — `verify` 옵션, 디카페인 옵션 규칙

**Files:**
- Modify: `pipeline/http.py`, `pipeline/records.py`, `data/curated/brands.yaml`, `pipeline/normalize/menus.py`
- Test: `tests/test_http.py`, `tests/test_normalize_menus.py`

**Interfaces:**
- Produces: `PoliteClient(..., verify: bool = True)`; `BrandRecord.decaf_option_categories: list[str] = []`; `menu_decaf_option(brand: BrandRecord, category: str | None, is_decaf: bool) -> bool`; `brands_by_key(curated_dir) -> dict[str, BrandRecord]`(normalize_brands 결과를 key로).

- [ ] **Step 1: 실패하는 테스트** — `tests/test_http.py`:
```python
def test_polite_client_verify_flag_reaches_httpx(monkeypatch):
    import httpx
    from pipeline.http import PoliteClient
    seen = {}
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.update(kw) or real(**{k: v for k, v in kw.items() if k != "verify"}))
    PoliteClient(verify=False)
    assert seen["verify"] is False
```
`tests/test_normalize_menus.py`:
```python
def test_menu_decaf_option_rule():
    from pipeline.normalize.menus import menu_decaf_option
    from pipeline.records import BrandRecord
    b = BrandRecord(key="brand:x", name="x", decaf_available=True, verified_at="2026-09-27",
                    decaf_option_categories=["에스프레소"])
    assert menu_decaf_option(b, "에스프레소", is_decaf=False) is True
    assert menu_decaf_option(b, "에스프레소", is_decaf=True) is False      # already decaf
    assert menu_decaf_option(b, "콜드브루", is_decaf=False) is False       # no decaf shot for cold brew
    assert menu_decaf_option(b.model_copy(update={"decaf_available": False}), "에스프레소", False) is False


def test_brands_yaml_has_decaf_option_categories_for_menu_brands():
    from pipeline.normalize.menus import brands_by_key
    from pipeline import settings
    b = brands_by_key(settings.CURATED_DIR)
    for k in ("brand:hollys", "brand:coffeebean", "brand:ediya", "brand:paulbassett", "brand:compose"):
        assert k in b and isinstance(b[k].decaf_option_categories, list)
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_http.py tests/test_normalize_menus.py -q` → FAIL

- [ ] **Step 3: 구현**
  - `pipeline/http.py`: `__init__(..., verify: bool = True)` → `httpx.Client(..., verify=verify)`.
  - `pipeline/records.py` `BrandRecord`: `decaf_option_categories: list[str] = Field(default_factory=list)`.
  - `pipeline/normalize/menus.py`:
```python
def menu_decaf_option(brand: BrandRecord, category: str | None, is_decaf: bool) -> bool:
    """Can the guest ask for a decaf shot? Only for espresso-based categories the brand lists, never for a drink
    that is already decaf."""
    return bool(brand.decaf_available and not is_decaf and category in brand.decaf_option_categories)


def brands_by_key(curated_dir: Path) -> dict[str, BrandRecord]:
    return {b.key: b for b in normalize_brands(curated_dir)}
```
  - `data/curated/brands.yaml`: 5개 브랜드에 `decaf_option_categories`를 넣는다. 값은 각 브랜드 태스크(3~7)가 카테고리명을 확정하며 채우되, 이 태스크에서는 조사 기준 초안을 넣는다: 할리스 `[에스프레소]`, 커피빈 `[에스프레소]`, 이디야 `[에스프레소]`, 폴바셋 `[에스프레소]`, 컴포즈 `[커피]`. `notes`에 "[공식] 디카페인 샷 변경 카테고리: …" 문구를 붙인다. 적재(`pipeline/load.py`의 brands upsert)가 명시 컬럼만 쓰는지 확인하고, 새 필드는 DB에 넣지 않는다.

- [ ] **Step 4: 통과 확인 + 커밋** — `uv run pytest -q` → PASS. `git commit -m "feat(collect): PoliteClient verify 옵션, 브랜드별 디카페인 샷 변경 카테고리 규칙"`

---

### Task 3~7: 브랜드 수집기 + 정규화 (같은 모양, 브랜드마다 한 태스크)

| Task | 브랜드 | 시작 URL | 상세 필요 | 특이사항 |
|---|---|---|---|---|
| 3 | 할리스 `hollys` | `https://www.hollys.co.kr/menu/espresso.do` (+ 커피 카테고리 `.do` 페이지들: 목록 내 탭 링크로 확인) | 예: `https://m.hollys.co.kr/menu/menuView.do?menuDiv=ESPRESSO&idx=…` (목록에 카페인이 있으면 상세 생략) | 가장 깨끗. HOT/ICED 둘 다 표시 |
| 4 | 커피빈 `coffeebean` | `https://www.coffeebeankorea.com/menu/app.asp?category=13` (커피 카테고리 번호들은 `menu/list.asp` 네비에서 확인) | 아니오(목록에 카페인) | ASP, 인코딩 확인(EUC-KR이면 `response.encoding` 지정) |
| 5 | 이디야 `ediya` | `https://www.ediya.com/contents/drink.html?chked_val=…` (커피 카테고리 값 + "더보기" 페이지 파라미터 확인; JS AJAX면 그 엔드포인트를 PoliteClient로 호출) | 아니오 | "DECAF" 필터 값도 수집해 is_decaf 보강 가능 |
| 6 | 폴바셋 `paulbassett` | `https://www.baristapaulbassett.co.kr/menu/List.pb?cid1=A` | 예: `menu/View.pb?dpid=…` | **`PoliteClient(verify=False)`를 이 수집기 안에서만 생성**(`collect(out_dir, http)`의 `http`는 무시하고 `http._delay`만 물려받음). robots는 curl -k로 확인됨 |
| 7 | 컴포즈 `compose` | `https://composecoffee.com/compose` (11페이지, `?page=` 또는 AJAX) | 아니오 | 카페인 값 1건 이상 **브라우저에서 눈으로 확인**(Chrome 도구 사용 가능). 값이 표와 다르면 `caffeine_mg=None`로 두고 ADR/README에 사유 |

각 태스크의 **Files:** `pipeline/collect/web.py`(+registry), `pipeline/normalize/menus.py`(+`normalize/__init__.py` 등록), `tests/fixtures/menus/<brand>/`, `tests/test_normalize_menus.py`, `tests/test_collect.py`.

**Interfaces (모든 브랜드 동일):**
- `class <Brand>Collector: name = "<brand>"; collect(out_dir, http) -> list[Path]` — 커피 카테고리 페이지(와 필요 시 상세)를 `out_dir`에 저장. 첫 요청 전에 `http.allowed(url)`이 False면 `RobotsDisallowed`.
- `normalize_<brand>(snap: Path, collected_at: str) -> Normalized` — `menu_items`만 채움. `brand_key="brand:<brand>"`, `category`는 사이트 카테고리명(정규화된 한글, brands.yaml `decaf_option_categories`와 같은 표기), `decaf_option=menu_decaf_option(brands_by_key(settings.CURATED_DIR)["brand:<brand>"], category, is_decaf)`.
- `_normalizers()`에 `"<brand>": normalize_<brand>`, `ALL_COLLECTORS`에 인스턴스 추가.

- [ ] **Step 1: 실제 페이지 확인** — `uv run python -c` 또는 짧은 스크립트로 `PoliteClient`를 써서 시작 URL 1~2개를 받아 구조(카테고리 링크, 항목 마크업, 카페인 표기, 페이지네이션/AJAX)를 확인한다. robots가 막거나 목록이 403이면 여기서 **BLOCKED**로 보고하고 멈춘다(사유 포함).

- [ ] **Step 2: 픽스처** — 받은 페이지에서 항목 3~6개(디카페인 1개 이상, HOT/ICED 쌍 1개 이상 포함)가 들어간 최소 마크업만 남겨 `tests/fixtures/menus/<brand>/`에 저장(파일당 30KB 이하). 상세가 필요한 브랜드는 상세 1~2개도 저장.

- [ ] **Step 3: 실패하는 테스트** — `tests/test_normalize_menus.py`에(브랜드명·수치는 픽스처에 맞춰 채운다; 아래는 모양):
```python
def test_<brand>_parses_names_caffeine_decaf(tmp_path):
    snap = _copy_fixture("menus/<brand>", tmp_path)          # 기존 헬퍼가 없으면 만든다: fixtures 디렉터리를 tmp에 복사
    items = normalize_<brand>(snap, "2026-09-27").menu_items
    by = {i.name: i for i in items}
    assert len(items) == <픽스처 항목 수, HOT/ICED 합친 뒤>
    assert by["<아메리카노 표기>"].caffeine_mg == <큰 값>            # HOT/ICED 중 큰 값
    assert by["<디카페인 항목>"].is_decaf and not by["<디카페인 항목>"].decaf_option
    assert by["<아메리카노 표기>"].decaf_option is <규칙상 값>
    assert all(i.brand_key == "brand:<brand>" and i.key.startswith("menu:<brand>:") for i in items)
    assert sum(i.caffeine_mg is not None for i in items) / len(items) >= 0.9
```
`tests/test_collect.py`에 수집기 테스트(기존 Mega/Paik 테스트 모양: 가짜 `http`로 저장 파일 목록 확인; 폴바셋은 `verify=False` 클라이언트 생성 여부를 monkeypatch로 확인).

- [ ] **Step 4: 실패 확인** — `uv run pytest tests/test_normalize_menus.py tests/test_collect.py -q` → FAIL

- [ ] **Step 5: 구현** — 수집기 + 정규화 + 등록. 이름 정리는 `clean()`, 카페인은 `re.search(r"카페인\s*([\d.,]+)\s*mg")` 류 + `num()`. HOT/ICED가 별도 항목이면 이름에서 "(HOT)/(ICED)/ICE/HOT" 접미·접두를 뗀 뒤 같은 이름끼리 합쳐 큰 카페인을 취한다 — **단, 사이트가 이름 자체를 다르게 쓰면(예: "아이스 아메리카노") 그대로 둔다**(우유 라벨 키와 일치해야 하므로 이름을 새로 만들지 않는다).

- [ ] **Step 6: 실제 수집 1회** — `uv run python -m pipeline run --only collect --source <brand>` → `data/raw/<brand>/<snapshot>/manifest.json`의 `ok: true`. 이어서 `--only normalize` 후 `data/normalized/menu_items.jsonl`에서 이 브랜드 항목 수·카페인 채움률·디카페인 수를 세어 보고서에 기록(채움률 < 90%면 파서 보강).

- [ ] **Step 7: 통과 확인 + 커밋** — `uv run pytest -q` PASS → `git commit -m "feat(collect): <브랜드> 커피 메뉴 수집·정규화(카페인·디카페인)"`

---

### Task 8: 적재, 우유 라벨, 평가, README, 운영 반영

**Files:**
- Modify: `data/curated/menu_milk_labels.yaml`(수기), `README.md`, `data/eval/phase2_violations.json`, `phase2_coverage*.json`
- Modify(필요 시): `app/core/scoring.py` `MILK_WORDS`, `app/eval.py` coverage에 브랜드별 메뉴 수

**Interfaces:**
- Consumes: Task 3~7의 normalize 결과. `coverage(repo)` 결과에 `"menu_items_by_brand": {brand_key: n}` 추가(`repo.coverage_counts`에 쿼리 추가).

- [ ] **Step 1: 적재** — `uv run python -m pipeline run --only load` (dev DB). `psql`로 `SELECT b.key, count(*) FROM menu_items m JOIN brands b ON b.id=m.brand_id WHERE m.active GROUP BY 1`을 보고서에 기록.

- [ ] **Step 2: 우유 라벨(수기)** — `SELECT DISTINCT name FROM menu_items WHERE active`에서 `menu_milk_labels.yaml`에 없는 이름을 파일로 뽑아(`.superpowers/unlabeled.txt`) **컨트롤러(사람 역할)가 직접** true/false를 붙여 yaml에 추가한다(머리말 기준). 서브에이전트는 이 단계에서 목록만 만들고 보고한다.

- [ ] **Step 3: golden test** — `uv run pytest tests/app/test_milk_labels.py -q`. 불일치가 있으면 `MILK_WORDS`에 단어를 더하거나(일반 규칙), 라벨을 재검토한다. 라벨을 규칙에 맞추려고 바꾸지 않는다.

- [ ] **Step 4: 평가** — `uv run python -m app.eval violations` → `violations == 0`; `coverage`/`coverage_open`(브랜드별 메뉴 수 포함). 결과 JSON 커밋.

- [ ] **Step 5: README** — 브랜드 표를 "메뉴 실측 8/10(스타벅스·메가·빽다방·할리스·커피빈·이디야·폴바셋·컴포즈), 브랜드 원두 추정 2(투썸: 봇 차단, 블루보틀: 카페 메뉴 미공개)"로 갱신. 조건 위반 표의 N 갱신.

- [ ] **Step 6: 운영 반영** — `DATABASE_URL=<neon; C:/githome/wine-sommelier_rag/.env의 NEON_DATABASE_URL> uv run python -m pipeline run --only load`. 코드는 main에 머지·푸시 → Render 자동 배포 후 `/health` 200 확인.

- [ ] **Step 7: 운영 확인** — 실제 세션(디카페인만·산미 4.5)으로 5개 브랜드 각각 `/recommend` 1회: 카드 3장, 실제 메뉴명, `explain_fallback` 수(총 15장 중 ≤ 1). 결과를 ADR 0004 끝의 "운영 확인" 절에 기록.

- [ ] **Step 8: 커밋** — `git commit -m "data: 5개 브랜드 메뉴 적재, 우유 라벨 추가, 위반 0/N 재평가, README 브랜드 표"`
