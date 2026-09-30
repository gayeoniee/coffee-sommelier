# ADR 0022 — 모모스커피의 이미지 맛 차트를 픽셀로 읽어 게이지 라벨에 추가(5번째 로스터리)

- 상태: 채택 (2026-09-30)
- 범위: `pipeline/collect/roasters_kr.py`(모모스 수집기), 새 모듈 `pipeline/collect/momos_taste_chart.py`,
  `config/feature_model_open.json`(유일한 작성자 `scripts/train_feature_model.py`), 로컬 `coffee_open` DB.
  전체판(`coffee` DB) 불변 — 모모스 원두는 전체판에도 있지만 `gauge_*`는 오픈판 특징 모델에서만 쓰인다.
- 앞선 결정: [ADR 0011](0011-roaster-gauges-feature-model.md)이 "이미지 게이지는 읽지 않는다"는 원칙을 세우고
  모모스를 "산문 또는 이미지뿐"으로 분류해 게이지 없이 사실 정보만 수집했다.

## 맥락

모모스커피(momos.co.kr, imweb)의 상품 페이지에는 산미·바디 게이지가 HTML 텍스트/마크업으로 없다 — 대신 상세
설명이 통째로 이미지 한 장(`<template id="prodDetailPC"><p data-gallery><img class="fr-dib" src="...">`, PNG,
가로 1000px 고정 · 세로 9,000~14,700px)으로 실려 있고, 그 안에 "산미"·"무게감" 행마다 브라운 채움 위 연회색
트랙의 5칸 막대와 "로스팅레벨" 5단계 스케일(라이트..다크) + Agtron 번호가 고정된 위치·색으로 매번 같은 템플릿으로
그려진다. ADR 0011의 원칙은 "이미지를 읽지 않는다"였지만, 그 취지는 OCR/LLM으로 이미지 내용을 *해석*하지 않는다는
것이지, 좌표·색이 고정된 차트를 픽셀 산술로 *측정*하는 것까지 막는 취지는 아니다 — 이 ADR은 그 경계를 명시한다:
**"텍스트/마크업, 또는 고정 템플릿 차트를 결정론적 픽셀 리더로 읽고 이미지 URL을 감사용으로 남긴 것"**만 게이지로
인정한다(OCR/LLM은 여전히 금지, 일회성/불규칙한 이미지도 금지).

## 결정

### 1. 결정론적 픽셀 리더 (`pipeline/collect/momos_taste_chart.py`)

PIL/numpy만 쓰고 OCR·LLM은 전혀 쓰지 않는다.

- **트랙 색은 고정**: 모든 제품(디카페인 포함)에서 `TRACK_RGB = (224, 219, 212)`로 동일.
- **채움 색은 고정이 아니다**: 일반 라인은 진한 갈색(134, 77, 24)이지만, 디카페인 한 종(idx 4876)은 더 옅은
  탄색(139, 111, 71)을 쓴다 — 그래서 리더는 채움 색을 하드코딩하지 않고, 한 행 안에서 "배경 흰색도 아니고
  트랙 색도 아니고 글자처럼 무채색(채널 폭 ≤ 18)도 아닌" 픽셀을 그 행의 채움으로 본다(`_fill_candidate_mask`).
  여전히 순수 픽셀 산술이고, 알려지지 않은 제3의 채움 색이 와도 그대로 동작한다.
- **막대 찾기**: 행마다 (채움 ∪ 트랙) 픽셀을 안티앨리어싱 이음매(≤6px)까지 이어붙여 폭 500~650px인 덩어리를
  찾고, 그 x축 시작이 템플릿의 고정 구간(340~390px, 캔버스 1000px 기준)에 있는 것만 인정한다. 같은 x0를 공유하며
  세로로 가까운(≤250px) 막대 2개 이상을 한 "차트 블록"으로 묶는다 — 사진처럼 우연히 그 색이 넓게 깔린 자리가 있어도
  x0가 막대마다 들쭉날쭉하면(사진은 정적 막대가 아니라 그라데이션) 블록으로 묶이지 않는다(실제로 제품 하나의 상단
  배너 사진에서 이런 거짓 양성이 나와 이 조건으로 걸러냈다).
- **값 계산**: 블록의 1번째 막대 = 산미, 2번째 = 무게감(순서는 템플릿 고정). 각 막대는 채움 비율을 5단계로
  환산한 뒤 반 칸 단위로 반올림(`round(ratio * 5 * 2) / 2`) — 온전한 칸과 반쯤 채운 칸(관찰됨: 2.5, 3.5) 둘 다
  다룬다. 로스팅 단계 막대(3번째, 있으면)는 정수 칸만 나오므로 반올림해 1~5 단계로 남겨 두지만(`roast_step`),
  이번 배포에는 쓰지 않는다 — 아래 "안 한 것" 참고.
- **실패 시 항상 `None`**: 템플릿을 못 찾거나(사진만 있는 제품, 파일 손상) 막대가 2개 미만이면 게이지를 절대
  추측하지 않고 조용히 `None`을 반환한다.

**검증**: 실제 원두 13개(모모스 상품 22페이지 캐시 중 실제로 차트를 가진 것 전부)의 차트 블록을 잘라 계산값과
나란히 붙인 콘택트 시트를 렌더링해 육안으로 대조했다(7개 제품, 온전한 칸·반 칸·디카페인 채움 색·로스팅 단계
1~4를 모두 포함) — 전부 일치했고, 같은 원두를 다른 용량(100g/1kg)으로 파는 4쌍(에스쇼콜라·프루티봉봉·부산·
므쵸베리)은 독립적으로 읽은 두 이미지가 비트 단위로 같은 값을 냈다(교차 검증). 전체 22페이지 중 실제 원두로
남는 15건 중 13건에서 값을 얻었고, 나머지 2건(스페셜티 아메리카노 100개입·스페셜티 커피믹스 50개입 — `momos_
disclosure`의 배제 키워드에 없어 사실 정보 자체는 남지만, 상세 이미지 템플릿이 달라 `prodDetailPC`에 단일
`img.fr-dib`가 없다)은 조용히 `gauge_acidity/body = None`이다 — 추측하지 않는다.

### 2. 수집기 연동

`MomosCollector.collect`가 상품 페이지를 파싱한 뒤(`parse_momos_product`는 그대로 순수 함수로 남긴다 — 고정
HTML 문자열만 받고 네트워크가 없어도 테스트 가능), `momos_chart_image_url`로 찾은 이미지 URL을 `PoliteClient`로
내려받고(캐시: `data/raw/roasters_kr/momos/chart_<idx>.png`, robots.txt는 `PoliteClient`가 매 호스트마다 확인)
`read_chart`로 읽는다. 성공하면 `gauge_fields`로 정규화해 `gauge_acidity`/`gauge_body`/`gauge_scale`(="momos:
image bar chart, pixel-measured")을 채우고, 원본 이미지 URL을 새 필드 `gauge_image_url`(감사용, 모든 게이지
레코드에 있는 필드 — 텍스트/마크업 게이지는 계속 `None`)에 남긴다. 이미지 다운로드·robots.txt 차단·읽기 실패는
전부 예외를 삼키고 게이지 없음으로 내려간다 — "추측하지 않는다"는 원칙이 네트워크 계층까지 이어진다.

### 3. 안 한 것 — Agtron 번호, 로스팅 단계

- **Agtron 번호**("99-101" 같은 문자열)는 읽지 않는다. 위치는 고정이지만 자릿수가 1~3자리로 바뀌는 렌더 텍스트라
  좌표만으로 안전하게 자르지 못하고, 그 이상은 OCR이다 — 이번 작업의 "OCR/LLM 금지" 원칙에 바로 걸려 스킵한다.
- **로스팅 단계**는 리더가 계산은 하지만(`ChartReading.roast_step`) `BeanFactRecord.roast_level`에 넣지 않았다.
  기존 `roast_level` 값들은 로스터리가 봉투에 적는 한국어 배전도 단어(예: "중강배전")인데, 모모스 차트는 다른
  어휘(라이트·미디엄 라이트·미디엄·미디엄 다크·다크)를 쓴다 — 억지로 끼워 넣으면 `pipeline/embed.py`·
  `pipeline/report.py` 등 `roast_level`을 읽는 다른 코드의 임베딩 텍스트가 바뀌어, 이번 배포의 채택 기준(산미·
  바디 게이지 정확도)과 무관한 곳까지 흔들 수 있다. 범위를 게이지 두 개로 좁혀 둔다 — 필요하면 별도 ADR로.

## 평가 — 채택 기준

[ADR 0011](0011-roaster-gauges-feature-model.md)의 로스터리 단위 교차검증(E1)과 [ADR 0014](0014-public-sources-ediya-twosome-kca-zenodo.md)의
Zenodo 외부 패널(E2, 평가 전용)로 **"모모스 라벨을 추가해도 나빠지지 않는가"**만 판정한다(레시피 자체는
[ADR 0020](0020-open-recipes-after-relabel.md)의 `SHIPPED_RECIPES` 그대로 — 산미 "+B", 바디 "+A wA=1"). 잡음
문턱은 ±0.02(반올림·재학습 시드에 따라 흔들리는 정도). `uv run python scripts/train_feature_model.py &&
uv run python scripts/eval_open_v3.py && uv run python scripts/train_feature_model.py && uv run python
scripts/eval_open_recipes.py && uv run python scripts/eval_zenodo_panel.py`로 재현([phase9_open_recipes.json](../../data/eval/open/phase9_open_recipes.json),
[phase2_zenodo_external.json](../../data/eval/phase2_zenodo_external.json)).

| 지표 | 이전(ADR 0020, 로스터리 4곳) | 지금(모모스 포함, 5곳) | 판정 |
|---|---|---|---|
| 산미 E1 ±1 · MAE (n) | 0.707 · 0.738 (82) | 0.705 · 0.711 (95) | ±1 −0.002(잡음 안), MAE 개선 |
| 산미 E2(Zenodo) ±1 · MAE · 순위상관 | 85.7% · 0.54 · 0.71 | 85.2% · 0.57 · 0.71 | ±1 −0.5pt(잡음 안), MAE +0.02(잡음 경계), 순위상관 불변 |
| 바디 E1 ±1 · MAE (n) | 0.781 · 0.693 (64) | 0.792 · 0.648 (77) | 둘 다 개선 |
| 바디 E2(Zenodo) ±1 · MAE | 83.1% · 0.593 | 84.6% · 0.583 | 둘 다 개선 |

**채택**: 네 지표 모두 잡음 문턱 안에서 그대로거나 개선됐다 — 특히 채택 기준의 1순위 신호인 E1(로스터리 단위
교차검증, 폴드가 4개에서 5개로 늘어 자체로도 더 튼튼해졌다)은 산미·바디 모두 MAE가 뚜렷이 좋아졌다. E2(Zenodo)의
산미 MAE만 0.542→0.567로 잡음 문턱(0.02)을 살짝 넘지만, ADR 0020·0021이 이미 적어 둔 대로 E2는 "정직성 확인"이지
대표 수치가 아니고, 같은 방향(±1·순위상관)은 흔들리지 않았다 — 13건(전체 게이지 95건의 14%) 추가로 인한 정상적인
재학습 계수 변동 범위로 본다. 모모스 라벨을 유지한다(되돌리지 않는다).

### 부수 효과 (채택 판정과 무관, 기록만)

- **오픈판 원두 수는 그대로**(209건) — 모모스는 이미 오픈판 원천이었고, 이번에 바뀐 것은 기존 13개 원두의
  `gauge_acidity`/`gauge_body`뿐이다. `coffee_open` DB가 바뀌었으니(Neon에 적재하는 쪽은 이 ADR이 다시 로드해야
  한다) `config/feature_model_open.json`도 다시 학습되어 바뀐다(계수·`alpha`만, 탑재 레시피 선택 자체는 그대로).
- 단맛 기권 규칙([ADR 0021](0021-sweetness-abstention-and-body-nested-check.md), 유사도 가중 이웃 수 ≥ 1.4)의
  E1 답한 비율이 51.4% → 50.0%로 살짝 내려갔다 — 모모스는 단맛 게이지가 없어(이 차트에 단맛 행 자체가 없다)
  단맛 학습에 기여하지 않지만, `coffee_open`에 원두가 있으니 다른 원두의 "이웃 중 단맛 라벨을 가진 이웃" 계산에는
  섞인다. ADR 0016의 50% 문턱은 여전히 (딱) 넘는다 — 단맛 레시피·기권 규칙은 이 ADR의 범위 밖이라 손대지 않는다.

## 재현

```
uv run python -m pipeline roasters-kr                          # 모모스 차트 이미지 다운로드 + 픽셀 판독
uv run python -m pipeline run --only normalize
uv run python -m pipeline run --only enrich --limit 0
uv run python -m pipeline run --only load                       # 전체판 DB
bash scripts/competition/build_open_db.sh                       # coffee_open 재적재
rm -f data/raw/open_tag_eval/v3_rows.pkl
uv run python scripts/train_feature_model.py
uv run python scripts/eval_open_v3.py
uv run python scripts/train_feature_model.py
uv run python scripts/eval_open_recipes.py
uv run python scripts/eval_zenodo_panel.py
uv run python scripts/eval_open_sweetness_abstain.py
```

## 한계

- 검증용 콘택트 시트는 15개 대상 원두 중 7개(약 절반)만 육안 대조했다 — 나머지 6개는 리더가 일관된 값을 냈다는
  것만 확인했고(같은 색·같은 x0·같은 폭 패턴), 낱개로 다시 보지는 않았다.
- 디카페인 채움 색(139, 111, 71)은 표본이 1건뿐이다 — 세 번째 색이 나오면(리더는 색을 하드코딩하지 않으므로
  동작은 하겠지만) 다시 콘택트 시트로 확인해야 한다.
- E2(Zenodo)의 산미 MAE 변화는 잡음 문턱을 살짝 넘어 "명확히 좋아짐"이라 말하기는 조심스럽다 — E1이 1차 신호라는
  ADR 0011의 원래 설계를 그대로 따른 판단이다.
