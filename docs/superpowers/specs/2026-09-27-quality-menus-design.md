# 실사용 품질: 빠른 설명 + 5개 브랜드 실제 메뉴 (설계)

2026-09-27. 2단계(추천 + 기록, 배포 완료) 위의 첫 번째 완성도 작업. 로드맵의 나머지: AI 엔지니어링 깊이 → 공모전 → 포트폴리오 마감.

## 1. 문제

1. **설명이 템플릿으로 떨어진다.** 운영에서 카드 8장 중 3장이 `explain_fallback`. 원인은 확인됨: 설명 모델(`nvidia/nemotron-3-super-120b-a12b`)이 기본값으로 "생각(thinking)"을 먼저 하고, 그 동안 첫 토큰이 5~20초 뒤에 온다(전체 마감 12초). 생각을 끄면 같은 프롬프트에서 첫 토큰 0.7초, 전체 1.7초(2026-09-26 측정, `chat_template_kwargs: {enable_thinking: false}`). 생각을 켠 채로는 포르투갈어 단어("embora", "parcialmente")가 본문에 새는 것도 관찰됨.
2. **브랜드 10개 중 7개는 메뉴가 없다.** 스타벅스·메가·빽다방만 메뉴(이름·카페인·디카페인 옵션)가 있고, 나머지는 `brand_items`가 만드는 합성 항목(아메리카노·카페라떼 두 개)뿐이라 카드에 실제 메뉴명이 안 나오고 `caffeine_rule=low` 필터가 카페인 수치 없이 돈다.

## 2. 목표 / 비목표

목표
- 운영 추천 카드의 설명 폴백 비율 < 5% (벤치: 첫 토큰 중앙값 < 2초).
- 할리스·커피빈·이디야·폴바셋·컴포즈 5개 브랜드의 커피 음료 메뉴(이름, 카페인 mg, 디카페인 여부/옵션)를 기존 3개와 같은 경로(collect → normalize → load)로 적재. 각 브랜드 커피 음료 카페인 채움률 ≥ 90%.
- 새 메뉴명 전부 우유 수기 라벨(`menu_milk_labels.yaml`)에 추가 → 조건 위반 평가 0/N 유지.
- 운영(Neon/Render)에 반영하고 실제 요청으로 확인.

비목표
- 투썸플레이스: robots.txt에 일반 규칙이 없고 목록 페이지가 봇을 403으로 막음 → 이번엔 제외(합성 항목 유지). 블루보틀: 공식 사이트에 카페 음료 메뉴 자체가 없음 → 제외.
- 가격, 사이즈별 카페인(HOT/ICED 둘 다 있으면 **더 큰 값**을 저장 — 조건 필터는 보수적으로), 시즌 메뉴 추적.
- 식약처 영양성분 DB: API 키 필요 + 프랜차이즈 음료 수록 여부 미확인 → 이번엔 안 씀(공모전 단계에서 교차검증 후보로만 언급).

## 3. 설명 속도

- `config/models.yaml`의 `explain` 태스크에 `extra: {chat_template_kwargs: {enable_thinking: false}}`.
- `parse_note`·`parse_bean`(JSON 파싱, 분석 흐름의 카드 앞 단계)은 **측정 후 결정**: 기존 few-shot 예문(`tests/app/test_parse_explain.py`의 케이스 + `note_messages` 예문 6개 이상)을 생각 on/off로 각 3회 돌려 파싱 결과 일치율과 지연을 기록. 일치율이 같거나 높으면 끈다. 결과는 `data/eval/phase2_thinking.json`과 ADR 0004에 기록.
- 마감 12초는 유지. `python -m app.eval bench`를 다시 돌려 README 표 갱신.

## 4. 메뉴 수집

### 4.1 소스 (2026-09-26 조사)

| 브랜드 | 경로 | robots | 형식 | 카페인 | 디카페인 | 주의 |
|---|---|---|---|---|---|---|
| 할리스 `hollys.co.kr` | `/menu/espresso.do` 등 카테고리 목록 + `m.hollys.co.kr/menu/menuView.do?menuDiv=…&idx=…` 상세 | `/membership`, `/myHollys`만 차단 | 서버 렌더 HTML, 영양표 | 목록·상세 모두, HOT/ICED | 디카페인 SKU 명시 | 가장 깨끗 |
| 커피빈 `coffeebeankorea.com` | `/menu/app.asp?category=13` 등 카테고리별 | `Allow: /` | ASP 서버 렌더 | 카테고리 목록에 표시 | 미확인 → brands.yaml 규칙 | |
| 이디야 `ediya.com` | `/contents/drink.html?chked_val=…` + "더보기" 페이지네이션 | 관리 경로만 차단 | HTML 카드(펼침) | 카드 안 텍스트 | "DECAF" 필터 존재 | 펼침이 JS면 파라미터/AJAX 경로 확인 |
| 폴바셋 `baristapaulbassett.co.kr` | `menu/List.pb?cid1=A` → `menu/View.pb?dpid=…` | `/common/coupon/`만 차단 | HTML 목록+상세 | 상세 | 디카페인 메뉴 섹션 | **TLS 인증서 무효** → 이 호스트만 `verify=False` |
| 컴포즈 `composecoffee.com` | `/compose` 영양·알레르기 표(11페이지) | 게시판 등만 차단 | 표(HTML/AJAX) | mg 열 | 미확인 → brands.yaml 규칙 | 관찰된 185.81mg가 파싱 오류일 수 있음 → 브라우저로 1건 눈 검증 |

### 4.2 수집기

- `pipeline/collect/web.py`에 브랜드별 `*Collector`(dataclass, `name`, `collect(out_dir, http)`) 추가, `registry.ALL_COLLECTORS`에 등록. 기존 `PoliteClient`(robots 재확인, 1초 지연) 사용. 폴바셋은 `PoliteClient(verify=False)`를 그 수집기 안에서만 만든다(생성자에 `verify` 인자 추가).
- 원본은 `data/raw/<brand>/<snapshot>/`에 페이지 단위로 저장(기존 mega와 동일). 상세 페이지가 필요한 브랜드(할리스·폴바셋)는 목록에서 id를 모아 상세 N개를 저장한다(커피 카테고리만).
- 범위: **커피 음료 카테고리만**(에스프레소/콜드브루/디카페인/라떼류). 티·에이드·스무디·푸드는 수집하지 않는다(카테고리 단위로 거른다; 이름으로 거르지 않는다).

### 4.3 정규화

- `pipeline/normalize/menus.py`에 `normalize_<brand>(snap, collected_at) -> Normalized` 추가, `normalize/__init__._normalizers()`에 등록. `MenuItemRecord` 필드: `key=menu:<brand>:<site id 또는 이름>`, `brand_key`, `name`, `name_en`, `category`, `is_decaf=detect_decaf(name)[0]`, `decaf_option`, `caffeine_mg`, `source_url`, `collected_at`.
- `caffeine_mg`: HOT/ICED가 따로 있으면 큰 값. 사이즈가 따로 있으면 기본(레귤러) 사이즈.
- `decaf_option`(디카페인 샷으로 바꿀 수 있는가): 브랜드별 규칙을 `data/curated/brands.yaml`의 새 필드 `decaf_option_categories: [..]`(정규화된 카테고리명 목록)로 둔다. 항목이 그 카테고리에 있고 `is_decaf`가 아니고 `decaf_available`이면 true. 스타벅스의 코드 규칙은 그대로 둔다.
- 이름 정리: `clean()`; 접두어 "(HOT)/(ICED)" 등 사이트 표기는 유지(수기 라벨은 정확한 이름을 키로 쓴다).

### 4.4 우유 라벨

- 적재 후 `SELECT DISTINCT name FROM menu_items` 중 라벨 파일에 없는 이름을 뽑아 **사람이(=이 세션에서 내가) 하나씩** true/false를 붙여 `menu_milk_labels.yaml`에 추가. 기준은 파일 머리말 그대로. `tests/app/test_milk_labels.py`의 golden test가 새 이름도 덮는다(전체 라벨 vs `is_milk_drink` 비교; 불일치는 `MILK_WORDS` 보강으로 해결).

### 4.5 적재·평가

- `uv run python -m pipeline run --only collect,normalize,load`(단계 이름은 `__main__` 기준). 기존 upsert 경로. 보호 행·`active` 규칙 그대로.
- `python -m app.eval violations` → 0/N. `coverage`에 브랜드별 메뉴 수를 추가 표시.
- README "브랜드" 표: 메뉴 있음 8/10, 없음 2(투썸·블루보틀, 사유).

## 5. 앱 변화

- 없음. 메뉴가 생긴 브랜드는 `brand_items`가 자동으로 실제 메뉴를 쓴다. 카드의 `source`는 그대로 `brand_bean`(원두 속성은 브랜드 추정)이고, 이름·카페인·디카페인 옵션만 실측이 된다.
- 결과 카드 라벨 "브랜드 원두 기준(추정)"은 유지.

## 6. 검증

- 단위: 브랜드별 파서 테스트(트림한 HTML 픽스처, `tests/fixtures/menus/<brand>/`), decaf_option 규칙 테스트, `PoliteClient(verify=False)` 테스트, thinking 플래그가 페이로드에 실리는 테스트.
- 통합: 위 평가 3종 + 벤치. `uv run pytest -q` 전부 통과.
- 운영: 배포 후 실제 세션으로 5개 브랜드 각각 `/recommend` 1회 — 카드 3장, 실제 메뉴명, 폴백 0~1장 확인. 결과를 ADR 0004에 기록.

## 7. 운영 반영

- 메뉴는 `DATABASE_URL=<neon> uv run python -m pipeline run --only load`(docs/deploy.md의 카탈로그 갱신 절차). 코드는 main push → Render 자동 배포.

## 8. 위험

- 사이트 구조가 조사와 다름 → 수집기 하나가 실패해도 나머지는 진행(run_collect가 소스별로 격리). 실패 브랜드는 비목표 표로 이동하고 사유 기록.
- 컴포즈 카페인 값 신뢰성 → 브라우저로 눈 검증 1건 이상, 이상하면 해당 브랜드 카페인은 null로 두고 이름·디카페인만 적재.
- 생각 끄기가 파싱 정확도를 떨어뜨림 → 측정 결과로 결정(§3).
