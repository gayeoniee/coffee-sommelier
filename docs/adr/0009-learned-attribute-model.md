# ADR 0009 — 원두 속성(산미/바디/단맛) 예측을 학습형 회귀 모델로 교체

- 상태: 채택 (2026-09-27)

## 맥락
ADR 0008이 향미 태그 예측만 학습형 모델로 바꿨고, 산미·바디·단맛은 여전히 이웃(k=10) 가중 평균이었다
(`predict_from_neighbors`, [design-decisions.md 14번(구판)](../design-decisions.md) — 이 ADR을 반영해
지금은 속성 학습 모델을 다룬다). 태그 모델과 같은 이유로
—운영이 실제로 보는 입력(태그 없는 사용자 텍스트)과 학습 입력의 분포를 맞추면— 속성도 같은 1024차원
태그 프리 임베딩 위에서 더 정확하게 예측할 수 있는지 확인했다.

## 결정
`scripts/train_attr_model.py`가 산미·바디·단맛 각각에 대해 릿지 회귀와 작은 MLP(1024→128→1)를 5-fold
CV MAE로 비교해 속성마다 독립적으로 더 나은 쪽을 고르고, `app/core/attrmodel.py`가 numpy 없이 순수
파이썬으로 순전파한다. `AttrModel.load()`가 없으면 `None`을 반환해 `app/graphs/analyze_bean.py`가 기존
이웃 평균으로 폴백한다 — ADR 0008과 동일한 폴백 규율.

### 학습 데이터
활성 원두 중 그 속성이 있는 모든 원두(대부분 `coffeereview_kaggle`), CQI 1,546건(사람 커핑 점수,
산미·바디만 있고 태그·단맛 없음)을 포함해 고정 held-out 200개(`repo.random_coffee_ids_for_loo(200,
seed=42, exclude_sources=("roasters_kr",))` — ADR 0008의 태그 모델과 **동일한 대상**)만 뺀다. 태그 프리
질의 임베딩 캐시(`data/embedded/<model>/tagfree_query.jsonl`)를 CQI 등 태그 없는 원두까지 커버하도록
확장했다(약 1,600건 추가, ~50요청); held-out 200개의 임베딩은 ADR 0008이 이미 만들어 둔
`data/eval/loo_tagfree_query_embeddings.jsonl`을 그대로 재사용해 새로 임베딩하지 않았다.

## 결과 — 전체판, 고정 held-out 200개, 같은 태그 프리 질의 임베딩으로 채점
5-fold CV MAE로 속성마다 릿지·MLP 중 더 나은 쪽을 골랐다(`data/eval/phase2_attr_model.json`):

| 속성 | 학습 표본 | CV MAE (릿지 / MLP) | 선택 | held-out MAE (모델 / 이웃) | held-out ±1 이내 (모델 / 이웃) |
|---|---|---|---|---|---|
| 산미 | 7,668 | 0.6652 / **0.6565** | MLP | **0.6231** / 0.7336 | **0.81** (n=200) / 0.7273 (n=198) |
| 바디 | 8,798 | 0.8217 / **0.8193** | MLP | **0.7649** / 0.8831 | **0.725** (n=200) / 0.645 (n=200) |
| 단맛 | 4,745 | **0.4787** / 0.4945 | 릿지 | **0.4393** / 0.5618 | **0.9293** (n=99) / 0.8854 (n=96) |

세 속성 모두 학습 모델이 이웃 평균을 MAE·±1 정확도 둘 다에서 이긴다 — 태그 모델(ADR 0008)만큼 극적이진
않지만(속성은 애초에 이웃 평균도 어느 정도 통했다) 뚜렷한 개선이다. `python -m app.eval loo`로 운영
코드 경로(순수 파이썬 순전파)로 다시 재면 산미 MAE 0.6233, 바디 0.7644, 단맛 0.4395 — sklearn 원본과
5자리 반올림 가중치 사이의 미세한 차이(1e-3 단위)뿐, 결론은 바뀌지 않는다(`data/eval/phase2_loo.json`
`attrs_model`).

## Goal B — 오픈판(라이선스 클린)

### B1. 오픈판 속성 모델
`coffeereview_kaggle`·`roasterdb`를 빼고 CQI + `roasters_kr` + `shopify`만으로 학습했다
(`--variant open`). 단맛은 오픈 라이선스 라벨이 36건뿐이라(최소 기준 300건) 오픈판 모델에서 뺐다 —
`AttrModel.predict()`가 `sweetness`에 `None`을 돌려주면 `app/graphs/analyze_bean.py`가 이웃 평균으로
폴백한다(`config/attr_model_open.json`에는 `acidity`/`body`만 있다).

| 속성 | 학습 표본 | CV MAE (릿지 / MLP) | 선택 | held-out MAE (모델 / 이웃) | held-out ±1 이내 (모델 / 이웃) |
|---|---|---|---|---|---|
| 산미 | 1,373 | **1.0549** / 1.0837 | 릿지 | 1.14 / **1.0981**(모델이 더 나쁨) | 0.45 / **0.5**(모델이 더 나쁨) |
| 바디 | 1,379 | **1.0105** / 1.0385 | 릿지 | **0.9919** / 1.0286 | 0.54 / **0.545**(근소하게 이웃이 나음) |
| 단맛 | 36 | — 최소 기준(300) 미달, 탑재 안 함 | — | — | — |

전체판만큼 뚜렷한 우위가 아니다 — 학습 표본이 훨씬 작고(대부분 CQI), CQI 자체가 산지·가공 텍스트가
같은 원두가 많아(중복 임베딩, [design-decisions.md #10](../design-decisions.md)) 릿지가 배울 수 있는
신호가 제한적이다. **산미는 모델이 이웃 평균보다 오히려 근소하게 나쁘다** — 정직하게 기록하고, 그래도
탑재한 이유는 (1) 스펙이 비교 게이트를 요구하지 않았고(태그 모델의 오픈판 게이트, 아래 B2와는 다름),
(2) 바디는 근소하게 낫고, (3) 성능이 비슷한 수준이라면 신뢰도·근거 표시는 그대로 이웃 평균 경로가
동일하게 동작해 사용자 체감 리스크가 크지 않기 때문이다.

### B2. 오픈판 향미 카테고리 모델 — 측정 결과 기각(미탑재)
개별 태그는 오픈 라이선스 태그 보유 원두가 너무 적어(`roasters_kr` + `shopify` ~48건) 애초에 불가능하다
(ADR 0008은 학습 풀 7,327건에서도 태그당 최소 30건이 필요했다). 대신 SCA 7대 카테고리
(`app.core.flavors.PREFERENCE_CHIPS`) 단위 다중 라벨 분류기가 가능한지만 쟀다 — 측정 풀은 `roasters_kr`
+ `shopify` + `roasterdb`(라이선스상 배포 불가, 측정에만 사용) 145건.

| 방법 | 카테고리 F1 |
|---|---|
| 5-fold CV MLP(1024→128→7, threshold 0.5로 스윕 중 최고) | 0.6459 |
| 이웃 투표 (같은 145건, 전체 카탈로그 이웃 k=10) | **0.6593** |

margin(CV − 투표) = **-0.0134** — 기준(≥0.05)에 크게 못 미치는 정도가 아니라 **이웃 투표가 오히려
근소하게 낫다**. `config/tag_model_open.json`을 탑재하지 않는다 — 오픈판은 지금처럼 이웃 투표만 쓴다.
145건으로 1024차원 임베딩 위에서 7-way 다중 라벨을 배우는 것보다, 전체 카탈로그(9천여 건)를 이웃으로
쓰는 투표가 정보량 면에서 유리하다는 뜻으로 읽는다(`data/eval/phase2_tag_model_open_feasibility.json`).

## 선택 로직
`app.config.DATA_VARIANT == "open"`이면 `config/attr_model_open.json`을, 아니면 `config/attr_model.json`을
읽는다(`app/core/attrmodel.py::AttrModel.load()`). 향미 태그 모델도 같은 방식으로 바뀌었다 —
`TagModel.load()`가 오픈판에서는 `config/tag_model_open.json`**만** 찾고(있을 때만), coffeereview 파생
전체판 파일(`config/tag_model.json`)로는 절대 폴백하지 않는다(ADR 0008의 라이선스 게이트 유지). 운영자가
`TAG_MODEL=off` / `ATTR_MODEL=off`로 두 모델 각각을 끌 수 있다.

## 크기·지연
- `config/attr_model.json`(전체판): 2,420 KiB → 2MB 예산 초과라 자동으로 `attr_model.json.gz`(682 KiB)로
  전환됐다(산미·바디가 MLP를 골라 1024→128 가중치 두 벌이 대부분을 차지한다). `config/attr_model_open.json`
  (오픈판, 릿지 둘뿐): 19.1 KiB, gzip 불필요.
- 순수 파이썬 순전파 지연: 실제 학습된 가중치로 200회 반복 측정, 세 속성(산미·바디 MLP + 단맛 릿지)을
  모두 예측하는 `AttrModel.predict()` 평균 **13.8ms/호출** — 50ms 예산의 28%. 태그 모델(7.1ms)과 합쳐도
  20.9ms로 여전히 예산 안이다.
- Docker 이미지: `docker build -q -t coffee-api:check .`로 만든 이미지에서 두 모델 모두 로드를 확인했다
  — 전체판(`docker run --rm coffee-api:check ...`): `AttrModel.load()` → acidity/body/sweetness 3개,
  `TagModel.load()` → 54개 태그. 오픈판(`-e DATA_VARIANT=open`): `AttrModel.load()` → acidity/body
  2개(단맛 없음), `TagModel.load()` → `None`(로그: "no learned tag model found ... tag_model_open.json") —
  둘 다 기대한 대로다. 이 커밋 전(799e496) 이미지와 비교하면 **358MB → 359MB(+1MB)** — gzip된 가중치
  파일(682KB + 19KB)이 대부분이고, scikit-learn/numpy는 여전히 `pipeline` 그룹 전용이라 이미지에
  들어가지 않는다.

## 유지한 것
- **향미 태그·이웃 근거·신뢰도는 그대로다.** `with_model_attrs`는 `predict_from_neighbors`가 낸 acidity/
  body/sweetness만 모델 값으로 바꾸고(모델이 커버하지 않는 속성은 이웃 평균 유지), confidence·tags·
  evidence·n_neighbors는 손대지 않는다.
- **임베딩 실패 시 폴백도 그대로다.** `deps.embed()`가 실패하면 여전히 이웃 평균(신뢰도 low)으로 돌고,
  학습 모델은 아예 보지 않는다.
- **`config/attr_model.json`이 없으면** `AttrModel.load()`가 `None`을 반환하고 경고를 한 번만 로그로
  남긴 뒤 이웃 평균으로 돈다.

## 재학습 방법
```bash
uv run python scripts/train_attr_model.py --variant full   # 전체판
uv run python scripts/train_attr_model.py --variant open   # 오픈판(라이선스 클린)
uv run python scripts/eval_tag_model_open.py                # Goal B2 카테고리 모델 측정/탑재 여부
uv run python -m app.eval loo                                # attrs_model 반영해 phase2_loo.json 갱신
```

## 결정하지 않은 것 / 알려진 한계
- 5-fold CV는 태그 모델과 마찬가지로 층화(iterative stratification)가 아니라 일반 `KFold`다.
- 릿지·MLP 외 다른 회귀 모델(그래디언트 부스팅 등)은 비교하지 않았다 — 스펙이 지정한 두 후보만 비교했다.
- 오픈판 카테고리 모델(B2)이 탑재되지 않았다면, 그 이유는 표본 부족(측정 풀 ~150건)이지 방법론의 결함이
  아니다 — 이웃 투표가 활용하는 전체 카탈로그(9천여 건)에 비해 정보량이 압도적으로 적다.
