# ADR 0014 — 공개 출처 추가: 이디야 음료 페이지, 투썸 원두 게이지, 소비자원 차 음료 시험, Zenodo 외부 검증

- 상태: 채택 (2026-09-28)
- 범위: 전체판·오픈판 공통(데이터 출처). Zenodo 패널 데이터는 평가 전용.

## 맥락

메뉴를 음료 단위로 실측한 브랜드는 7곳이었고, 이디야는 "메뉴 데이터가 robots.txt 차단 경로(`/inc/`)로만 제공"된다고 보고
원두 카드만 냈다. 투썸은 "봇 차단"으로 원두 값 4개(산미·바디 × 하우스·디카페인)가 손 추정이었다. 공모전 제출본(오픈판)에
넣을 수 있는, 라이선스가 분명한 공개 사실을 더 찾았다.

## 결정

### 1. 이디야 공식 음료 페이지 (`EdiyaCollector`, `normalize_ediya`)

- `https://ediya.com/contents/drink.html`은 서버가 HTML로 카드를 그린다. 카테고리 체크(`chked_val`)와 검색어(`skeyword`)를
  함께 받아 **첫 8개 카드**만 그리고, "더보기"는 `/inc/ajax_brand.php`(robots.txt `Disallow: /inc/`)라 부르지 않는다.
  robots.txt가 막는 경로는 `/admin/ /manager/ /member/ /inc/ /checkplus/`뿐이라 이 페이지 자체는 허용이다.
- 그래서 카테고리 2개(12=COFFEE, 155=DECAF) × (검색어 없음 + 고정 검색어 22개)를 `PoliteClient`로 조회한다(46요청).
  검색은 메뉴 이름의 띄어쓰기를 그대로 맞춰야 해서("카페라떼" 0건, "카페 라떼" 8건) 검색어는 사이트 표기를 따른다.
  돌체·토피넛 라떼는 현재 메뉴에 없다.
- 카드마다 이름, `(L)`/`(EX)` 사이즈, HOT/ICED, 카페인 mg, 컵용량(ml)이 있다. 음료 하나 = 항목 하나로 합치고 **L(기본) 컵의
  카페인**, HOT/ICED 중 큰 값을 쓴다(컴포즈·할리스와 같은 규칙). 컵용량은 사이즈 판정에만 쓰고 저장 컬럼은 없다.
- DECAF 카테고리는 같은 이름 음료의 디카페인 SKU(카페인 3~50mg)다 → `디카페인 <이름>` 별도 항목. 이 탭의 차 음료
  (아샷추 복숭아 = "Peach Iced Tea with Espresso")는 커피 음료가 아니라 뺀다.
- "얼박샷추(디카페인 원두)"는 디카페인 원두지만 에너지 음료 때문에 163mg이다 → 앱의 저카페인 선(100mg)을 넘는 "디카페인"
  카드는 디카페인으로 보지 않고, 그 원래 음료(얼박샷추)도 "디카페인으로 변경" 대상에서 뺀다.
- DECAF 카테고리가 대표 커피 음료 대부분의 디카페인판을 싣는 것이 공식 근거라 `brands.yaml` 이디야
  `decaf_option_categories`를 `[COFFEE]`로 바꿨다(콜드브루처럼 샷이 없는 음료는 기존 `NO_SHOT_WORDS` 규칙으로 제외).
- 결과(2026-09-28): 41개 항목(COFFEE 22 · DECAF 19), 카페인 41/41, 디카페인 19. 우유 라벨 26건을 손으로 추가했다
  (`data/curated/menu_milk_labels.yaml`, 음료 설명·알레르기 표기로 판단).

### 2. 투썸 원두 게이지 (`data/curated/brand_beans_official.yaml`)

- `https://www.twosome.co.kr/mn/coffeeStory.do`에 블렌드 3종의 로스팅·산지·한국어 설명·영어 설명어와 **산미·바디 막대(%)**가
  있다. robots.txt에는 Disallow가 없지만 CloudFront가 봇 UA(`PoliteClient` 포함)를 403으로 막는다. 우회하는 수집기는 만들지
  않고, 공개 페이지를 브라우저로 한 번 열어 **사실만 손으로 옮겼다**(URL·확인일 기록).
- 막대는 `gauges_pct`로 적고 `scripts/derive_brand_beans.py`가 `1 + 4·p/100`(소수 한 자리)로 1~5에 옮긴다 → `official_gauge`.
  게이지는 라이선스 제약이 없는 브랜드 공식 사실이라 전체판·오픈판 모두 같은 값을 쓴다. 게이지가 없는 단맛·향미는
  [ADR 0012](0012-official-brand-beans.md)의 기존 규칙(전체판 문구>모델, 오픈판 문구>특징 모델)을 따른다.

| 원두 | 산미 | 바디 | 단맛 (전체/오픈) | 향미 |
|---|---|---|---|---|
| 블랙그라운드(하우스) | 2 → **2.6** (40%) | 4 → **5.0** (100%) | 2 → 2.5 / 5 | chocolate, smoky, nutty |
| 디카페인 | 2 → **2.6** (40%) | 3 → **4.2** (80%) | 3 → 4 / 4 | nutty |
| 아로마노트(선택 원두, 값에는 안 씀) | (80% = 4.2) | (60% = 3.4) | — | berry, floral |

## 재현

```bash
uv run python -m pipeline run --only collect --source ediya
uv run python -m pipeline run --only normalize --only load
uv run python scripts/derive_brand_beans.py --before <(git show bd0a6f6^:data/curated/brands.yaml)
uv run python scripts/derive_brand_beans.py --variant open
```
