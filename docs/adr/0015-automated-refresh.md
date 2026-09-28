# ADR 0015 — 데이터 자동 갱신: 주간 수집 → 차이 → 증분 보강 → 품질 게이트 → 게시

- 상태: 채택 (2026-09-28)
- 범위: 전체판·오픈판 공통(운영). 코드: `scripts/refresh/`, `.github/workflows/refresh.yml`

## 맥락

프랜차이즈 메뉴(8개 브랜드)와 로스터리 원두(국내 13곳 `roasters_kr`, 해외 Shopify 강도 표기 8곳 `shopify_gauged`)는
계절마다 바뀐다. 지금까지는 사람이 `pipeline run`을 돌리고 `migrate_to_neon.sh`로 옮겼는데, 이 스크립트는 운영 DB에
테이블이 있으면 `RESET=1`(사용자 기록까지 삭제) 말고는 길이 없었다. 손으로 하지 않아도 메뉴·원두가 최신으로 유지되되,
**망가진 스크레이퍼나 모델 차이가 운영 추천을 해치지 않게** 하는 장치가 필요했다.

## 결정

### 1. 한 진입점, 두 실행 환경

`scripts/refresh/refresh.py` 하나를 로컬과 GitHub Actions(매주 월 03:00 KST = 일 18:00 UTC, `workflow_dispatch`)에서 같이
쓴다. 모든 단계가 상태를 `data/refresh/<날짜>/report.{json,md}`에 남기고(원본은 gitignore), Markdown 요약이 PR·이슈 본문이
된다. `DRY_RUN=1`이면 운영 DB 쓰기와 PR이 없다(테스트와 첫 수동 실행용).

| 단계 | 하는 일 | 실패 처리 |
|---|---|---|
| collect | 브랜드 메뉴 8곳 + shopify + shopify_gauged + roasters-kr(+SCA 향미 휠). `PoliteClient`가 매 요청 robots.txt 확인·호스트별 지연 | 소스 하나의 실패는 기록만 하고 계속. 6일 안에 받은 스냅샷은 재사용(CI는 `data/raw`를 ISO 주차 키로 캐시) |
| normalize | 라이브 소스만 정규화. coffeereview·CQI·RoasterDB는 고정 데이터셋이라 다시 받지 않는다 | — |
| stage | 현재 카탈로그(운영 Neon, 로컬은 개발 DB)를 스테이징 DB로 복제 — `catalog_sync.py`, id 보존 | — |
| diff | 신규·삭제 메뉴, 카페인 변화(20% 초과 표시), 디카페인 표시 변화, 새 브랜드·로스터리, **라벨 필요**(우유 라벨 없는 새 메뉴명) | — |
| enrich/embed | **신규·내용이 바뀐 원두만** LLM 보강·임베딩. 나머지는 DB 행을 그대로 재사용 | LLM 호출 상한 `REFRESH_MAX_LLM_CALLS`(기본 300) |
| load | 스테이징에 범위 적재(`run_load(coffee_scope, brand_scope)`), 오픈판 스테이징은 `build_open_db.sh` 규칙으로 파생 | 이번에 행을 못 낸 그룹(로스터리·매장·브랜드)은 삭제 대상에서 제외 |
| gates | 아래 3절 — 전부 통과해야 게시 | 하나라도 실패하면 DB 쓰기 없음 + 이슈 |
| publish | 스테이징 → 운영 Neon(전체·오픈) 카탈로그 동기화, `refresh/<날짜>` PR(평가 JSON·README·초안 수치), 자동 병합(squash) | 자동 병합 실패는 치명적이지 않음(DB는 이미 게시) |

"내용이 바뀐 원두"는 소스가 직접 주는 필드(이름·로스터리·산지·가공·로스팅·요약·URL·고도·품종)가 다르거나, 소스가
직접 준 게이지/태그/디카페인 값이 DB와 다르거나, 리뷰 글이 달라졌거나, DB에 임베딩이 없는 경우다. enrich가 채운 값은
소스가 비워 두면 비교하지 않는다(매주 같은 원두를 다시 보강하지 않도록). 바뀌지 않은 행은 `collected_at`도 그대로 둬서
게시 때 실제로 바뀐 행만 쓴다.

### 2. 라벨 없는 새 메뉴는 닫힌 쪽으로(fail closed)

`data/curated/menu_milk_labels.yaml`(사람이 판단한 우유 포함 여부)에 이름이 없는 새 메뉴는 적재는 하되
`menu_items.needs_review = true`로 둔다. `app/repo.py`가 추천·브랜드 목록·커버리지에서 이 행을 뺀다 — 키워드 판정
(`is_milk_drink`)이 틀리면 우유를 피하는 손님에게 라떼가 추천될 수 있어서, 사람이 라벨을 달 때까지 추천하지 않는다.
라벨을 달면 다음 적재에서 플래그가 풀린다. 리포트에 "라벨 필요 N개"로 목록이 나온다. 컬럼은 기존 스키마 마이그레이션
방식(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)으로 추가했고, 컬럼이 아직 없는 DB에서도 앱이 돌도록 존재 여부를 한 번
확인한다(배포 순서 안전).

### 3. 품질 게이트 (모두 통과해야 게시)

| 게이트 | 기준 |
|---|---|
| size_check | 표(원두·메뉴)의 활성 행 중 **20% 넘게** 사라지거나, 수집된 그룹 하나가 5행 이상에서 절반 미만으로 줄면 실패(스크레이퍼 고장 의심) |
| violations_full / _open | 두 판 스테이징 DB에서 조건 위반 **0건**(`app.eval violations`) |
| loo_acidity_full / _open | LOO 산미 ±1 비율 ≥ 커밋된 기준선 − 0.02(`data/eval/phase2_loo.json`, `data/eval/open/phase2_loo.json`) |
| readme_numbers | 새 평가 JSON으로 README 데이터 수치·공모전 초안 수치 블록을 다시 쓴 사본이 `check_readme_numbers`를 통과 |
| pytest | 전체 테스트 |

LOO 허용폭 0.02는 스테이징에서 HNSW 인덱스를 새로 만들 때 생기는 근사 이웃 차이(같은 데이터로 0.745 ↔ 0.75)를 넘는
하락만 잡으려는 값이다.

### 4. 게시: 카탈로그만, 바뀐 행만 — `catalog_sync.py`

운영 DB에는 사용자 테이블(users·taste_profiles·tastings·profile_history)이 있으므로 `migrate_to_neon.sh`의 덤프/복원은
자동화에 쓸 수 없다. `scripts/refresh/catalog_sync.py`가 카탈로그 5개 표(flavor_taxonomy·coffees·brands·reviews·
menu_items)를 **키 기준**으로 맞춘다.

- 외래 키는 참조 행의 키로 옮긴다(DB마다 id가 달라도 된다). 새 행은 대상에서 비어 있으면 원본 id를 유지한다.
- 양쪽에서 행 md5를 SQL로 계산해 **다른 행만** 쓴다 — 매주 9천여 개 임베딩을 다시 쓰지 않는다.
- 원본에서 사라진 행은 지우되, 사용자 시식 기록(또는 남은 메뉴·브랜드 원두)이 가리키면 `active = false`로 남긴다
  (`pipeline/load.py`와 같은 규칙). 사용자 테이블은 읽기(보호 대상 조회)만 한다. 전부 한 트랜잭션.
- `--variant open`: coffeereview 원두·리뷰를 빼고 `brands.bean/decaf_bean`을 `*_open` 값으로 덮는다(오픈판 파생).
- 수동 배포도 같은 경로: `MODE=catalog scripts/deploy/migrate_to_neon.sh`(DRY_RUN=1이면 계획만).

작업 지시는 "전체판은 `--only load`로 적재"였지만 CI에는 전체 파이프라인 캐시(9천여 개 원두의 보강·임베딩, Kaggle 원본)가
없어 그대로는 돌 수 없고, 게이트를 통과한 **바로 그 스테이징 DB**를 옮기는 편이 검증과 게시가 어긋나지 않아 이 방식을 택했다.

### 5. CI의 보강 모델: `enrich_ci`

CI에는 로컬 Ollama(qwen3.5:9b)가 없어 `ENRICH_TASK=enrich_ci`(NVIDIA `nemotron-3-super-120b-a12b`, thinking 끔, 503
과부하는 재시도)로 보강한다. 같은 프롬프트로 라이브 소스 원두 50개(seed 42, qwen 캐시가 현재 텍스트와 일치하는 251개
중)를 다시 보강해 qwen 출력과 비교했다(`scripts/refresh/enrich_agreement.py` →
[enrich_ci_agreement.json](../../data/eval/enrich_ci_agreement.json)).

| 항목 | 값 |
|---|---|
| 향미 태그 (qwen 기준 마이크로) | 정밀도 0.628 · 재현율 0.689 · **F1 0.657** |
| 산미 ±1 일치 / 정확 일치 | **1.0** / 0.563 (둘 다 값을 낸 16개) |
| 바디 ±1 / 정확 | **1.0** / 0.696 (23개) |
| 단맛 ±1 / 정확 | **1.0** / 0.448 (29개) |
| 한쪽만 null | 산미 15(qwen null 12) · 바디 19(16) · 단맛 15(13) |
| 지연 p50 / 최대 | 1.7초 / 5.2초 (실패 0) |

둘 다 값을 내면 ±1 안에서 항상 일치하지만, nemotron은 qwen이 "근거 없음(null)"이라 한 원두의 약 4분의 1에도 값을 낸다.
그 값은 `attr_label_source = llm_review`로 표시되고, 신규·변경 원두에만 적용되므로 매주 몇 개 수준이다. 태그 일치도(F1
0.66)는 두 LLM이 같은 SCA 어휘에서 고르는 폭의 차이로, 앱의 태그 예측(학습형 모델, ADR 0008)은 이 값과 독립이다.

### 6. 월간 재학습 (첫 월요일, 자동 병합 안 함)

`retrain` 잡이 운영 카탈로그를 작업용 Postgres에 복제하고 `train_tag_model.py --pin-holdout --out-dir`,
`train_attr_model.py --variant full --pin-holdout --out-dir`, `train_feature_model.py --no-ship`(DATA_DIR 임시)을 돌려
커밋된 보류 지표(태그 F1, 속성 ±1, 특징 모델 CV ±1)와 비교한다. 한 지표라도 +0.01 이상 오르고 어떤 지표도 0.005 넘게
내려가지 않을 때만 `retrain/<날짜>` PR을 연다. 모델 교체는 사람이 검토해 병합한다.

## 실패 모드

| 상황 | 결과 |
|---|---|
| 한 브랜드/로스터리 스크레이퍼가 예외 | 그 소스 상태 `failed`, 그 그룹의 기존 행은 그대로(범위 적재), 나머지는 갱신 |
| 스크레이퍼가 조용히 절반만 가져옴 | size_check 실패 → DB 쓰기 없음, 이슈 |
| 새 메뉴명에 우유 라벨 없음 | needs_review로 적재, 추천 제외, 리포트 "라벨 필요" — 게시는 진행 |
| LLM/임베딩 API 장애 | 보강 실패 원두는 규칙 값만으로 적재(`enrich_log` 없음), 임베딩 실패는 예외 → 리포트·이슈, DB 쓰기 없음 |
| 게이트 실패(위반·LOO·수치·테스트) | DB 쓰기 없음, 이슈 "데이터 갱신 실패 <날짜>"(같은 날 재실행은 댓글) |
| 게시 중 오류 | 대상 DB마다 한 트랜잭션이라 롤백. 전체판 게시 후 오픈판에서 실패하면 전체판만 갱신된 상태 — 다음 주 실행이 오픈판을 맞춘다 |
| PR 자동 병합 불가(저장소 설정) | PR만 열린 채 남는다. 운영 DB는 이미 갱신, 커밋된 수치만 뒤처짐 |

## 비용 (주 1회)

- **NVIDIA API**: 임베딩 = 신규·변경 원두 / 32개당 1요청(평소 0~10요청), 보강 = 신규·변경 원두 중 값이 빈 것 1회씩(상한 300,
  평소 수십). 평가·테스트는 LLM을 부르지 않는다. 월간 재학습은 태그 프리 임베딩 캐시가 없으면 원두 전체를 다시 임베딩
  (약 300요청, `data/embedded`를 월 키로 캐시).
- **크롤링**: 브랜드 메뉴 수십~수백 요청 + 로스터리 제품 페이지(주 1회 새로, 같은 주 재실행은 캐시) — 호스트별 1~2초 지연.
- **Neon 읽기**: 스테이징 복제에 카탈로그 전체를 한 번 읽는다(임베딩 텍스트 약 115MB) + 게시 전 행 md5 비교.
  한 달 약 0.5~0.6GB(재학습 포함) — 무료 전송 한도 안.
- **Neon 쓰기**: 바뀐 행만(평소 수~수십 행). 새 컬럼 추가는 첫 게시 때 한 번.
- **GitHub Actions**: 공개 저장소라 무료. 갱신 잡 약 15~25분(크롤링이 대부분), 재학습 월 1회 약 30~60분.

## 결과

- 첫 DRY_RUN(로컬, 개발 DB 대상, 실제 수집 12개 소스 모두 ok, 1,124초): 메뉴 512개 변화 없음, 라이브 원두 417 → 415
  (신규 2 · 삭제 4 · 내용 변경 2, LLM 4회 · 임베딩 1요청), 라벨 필요 0, 게이트 7개 모두 통과(위반 0/108 두 판, LOO 산미
  0.745 / 0.525 = 기준선), 게시 계획은 두 판 모두 coffees +2/~2/-4 — 쓰기 없음.
  [docs/ops/refresh-sample-report.md](../ops/refresh-sample-report.md).
- 테스트: `tests/refresh/`(차이 계산, 게이트, catalog_sync, DRY_RUN 종단 간, 재학습 비교), `tests/test_db_load.py`
  (needs_review, 범위 적재), `tests/app/test_repo.py`(needs_review 필터, 컬럼 없는 DB).
