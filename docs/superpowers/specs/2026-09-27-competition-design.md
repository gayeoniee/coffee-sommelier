# 공모전 제출본: 오픈 데이터판 배포, CSV·이미지·초안 완성 (설계)

2026-09-27. 완성도 로드맵 3/4. 대상: 2026 데이터+AI 혁신 챌린지 · 데이터 문제해결 부문 · 데이터레시피 제안(마감 10.22). 요강·심사기준·초안은 `docs/competition/data-recipe-draft.md`, 할 일은 `docs/competition/checklist.md`.

## 1. 문제

초안은 있지만 제출할 수 없는 상태다:
1. **앱이 쓰는 DB에 라이선스 제한 데이터(coffeereview)가 들어 있다.** 평가 코드의 `exclude_sources`로만 빠져 있어, 심사위원이 데모 URL을 열면 제출본과 다른 데이터로 돈다.
2. 초안의 수치(위반율, LOO, 커버리지)가 전체판 DB에서 잰 값과 섞여 있다.
3. 포털이 요구하는 산출물 — CSV 3종(컬럼정의·템플릿·데이터셋), 결과 이미지 ≤5장, 300자 이상 서술 5개, 생성형 AI 고지 — 가 없다.

## 2. 목표 / 비목표

목표
- **제출본 배포**: coffeereview가 없는 DB(`오픈 + 국내 로스터리` 구성)로 도는 별도 배포(백엔드·웹). 심사위원용 URL 하나. 화면 상단에 "공모전 제출본(오픈 데이터)" 표시.
- **제출본 수치**: 그 DB에서 `violations`·`loo`·`coverage`·`convergence`·`bench`·`explain_quality`를 다시 재고 `data/eval/open/` 에 저장. 초안의 모든 수치를 이 JSON에서만 가져온다.
- **CSV 3종**: 포털 업로드용. 포함: CQI 원두(공개, CC), 국내 로스터리 사실정보(직접 수집, 사실만), 프랜차이즈 메뉴(공식 사이트 사실: 이름·카페인·디카페인), 브랜드, 우유 라벨(직접 제작), SCA 휠 한국어 이름(직접 제작). **제외: coffeereview·RoasterDB(CC BY-NC)·SCA 휠 원문 파생 텍스트.**
- **이미지 5장**: 초안 B-6의 1~5번을 실제 데이터로 그린다.
- **초안 완성**: 【작성 필요】 중 사용자 개인정보·드롭다운·전화 확인 항목만 남기고 나머지를 채운다. 서술 5개 항목 300자 이상 검사 스크립트.

비목표
- 공공데이터(식약처 API 등) 연동: API 키 발급이 사용자 계정 작업이라 이번엔 "확장 경로"로만 적는다.
- 포털 입력 자체(로그인 필요), 전화 확인, 팀 구성.

## 3. 오픈 데이터판 DB

- 파이프라인 normalize에 소스 제외 옵션: `uv run python -m pipeline run --only normalize,load --exclude-source coffeereview_kaggle` (`EXCLUDE_SOURCES` 환경변수도 동일). normalize가 그 소스를 읽지 않으면 reviews·coffees 모두 빠진다. enrich·embed 캐시는 키 기준이라 그대로 재사용(새 LLM 호출 없음).
- 로컬: `docker`의 Postgres에 `coffee_open` DB를 만들고 `DATABASE_URL=...coffee_open`으로 적재. `db/schema.sql` 적용은 기존 절차(`pipeline.db` 초기화)를 따른다.
- 운영: Neon 같은 프로젝트에 데이터베이스 `coffee_open` 추가(`neonctl databases create`), `scripts/deploy/migrate_to_neon.sh`를 소스 DB 이름 인자(`SRC_DB=coffee_open`)로 확장해 복사.
- 기대 규모: 원두 ≈ 1,762(CQI 1,546 + RoasterDB 100 + 로스터리 107 + 블루보틀 9), 메뉴 471, 저장 ≈ 30MB. RoasterDB(CC BY-NC)는 **앱 DB에는 남긴다**(비상업 사용, 출처 표기 — 초안 표에 그대로 기재)하되 **CSV 업로드에서는 뺀다**(포털이 재배포·AI 학습에 쓰므로).

## 4. 제출본 배포

- Render: 두 번째 무료 웹 서비스 `coffee-sommelier-open-api`(같은 Dockerfile, `DATABASE_URL`=Neon `coffee_open`, 나머지 env 동일). `render.yaml`에 서비스 추가 + `scripts/deploy/deploy_all.sh`에 `VARIANT=open` 경로(서비스명·DB명·Vercel 프로젝트명 접미사 `-open`).
- Vercel: 프로젝트 `coffee-sommelier-open`(같은 `web/`, `API_URL`=open 백엔드, `NEXT_PUBLIC_VARIANT=open`). 웹은 `NEXT_PUBLIC_VARIANT=open`일 때 상단에 작은 배너 "공모전 제출본 · 오픈 데이터 + 국내 로스터리 사실정보 (coffeereview 미포함)"를 보여준다(레이아웃 한 줄, 테스트 1개).
- CI의 Render 배포 트리거는 두 서비스 모두 호출한다.
- 백엔드 `/health`에 `variant`(env `DATA_VARIANT`, 기본 `full`)와 `coffees` 수를 넣어 배포 확인이 가능하게 한다.

## 5. 수치·CSV·이미지

- `data/eval/open/` : `python -m app.eval …` 결과를 `EVAL_DIR` 환경변수로 이 폴더에 쓴다(`settings.EVAL_DIR` env 오버라이드). `explain_quality`도 이 DB에서 1회.
- `scripts/competition/export_csv.py` → `data/competition/`:
  - `컬럼정의.csv`(변수번호, 타깃여부, 타입, 컬럼명, 비고 — 가이드 형식) — `coffees`·`menu_items`·`brands` 컬럼.
  - `데이터_템플릿.csv`(각 테이블 3행 예시).
  - `데이터셋_coffees.csv`(source ∈ {cqi, roasters_kr, shopify}), `데이터셋_menu_items.csv`, `데이터셋_brands.csv`, `데이터셋_milk_labels.csv`, `데이터셋_sca_ko.csv`. 리뷰·설명 원문 열은 넣지 않는다(`flavor_summary` 제외). 각 파일 첫 줄 헤더, UTF-8 BOM(엑셀 호환).
- `scripts/competition/figures.py` → `docs/competition/images/` 5장(matplotlib, 한글 폰트는 시스템 `Malgun Gothic`; 없으면 영문 라벨 폴백):
  1. `01_violations.png` 페르소나×브랜드 위반 히트맵(전부 0).
  2. `02_loo_compare.png` 산미·바디 ±1 정확도: 전체/오픈/오픈+로스터리 막대 + 재적재 잡음 범위 표시.
  3. `03_decaf_coverage.png` 디카페인 후보 수(6→13)와 디카페인+산미 페르소나 top5 표.
  4. `04_convergence.png` 모의 사용자 수렴 곡선.
  5. `05_latency_quality.png` 첫 토큰 지연(생각 on/off) + 설명 품질 판정 결과.
- `scripts/competition/check_draft.py`: 초안의 A-2~A-6 본문 글자 수(공백 제외) ≥ 300 검사, 【작성 필요】 남은 개수 출력.

## 6. 초안 갱신

- 모든 수치를 `data/eval/open/*.json`에서 읽어 넣는다(값을 손으로 적지 않는다: 표는 스크립트가 만든 마크다운 조각을 붙인다).
- B-6에 이미지 5장과 해석 문단. 3장 "생성형 AI 활용 고지" 문단 실제 범위로: 코드·문서 작성에 Claude Code 사용, 데이터 구조화에 로컬 qwen3.5 9B, 설명 생성·판정에 NVIDIA API 모델 — 모델명·용도 명시.
- 남는 【작성 필요】: 개인정보, 드롭다운 선택, 전화 확인 결과, 팀 구성.

## 7. 검증

- `uv run pytest -q`(제외 옵션·CSV 내보내기·배너 테스트). 오픈 DB `violations == 0`. 두 배포의 `/api/health`가 `variant`를 올바르게 반환. `check_draft.py` 통과(남은 항목은 사용자 몫만).
