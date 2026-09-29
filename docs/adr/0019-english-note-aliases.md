# ADR 0019 — 영어 노트 별칭: "caramel"·"nuts"·"berries"·"earthy"를 휠 태그로, 오픈 DB 재적재

- 상태: 채택 (2026-09-29)
- 범위: 두 판이 함께 쓰는 노트 매퍼(`pipeline/enrich.py` `rule_tags`) — 파이프라인 라벨링과 손님 입력 단서
  (`app/core/textcues.py` `text_tags`)가 같은 표를 읽는다. **전체판 라벨은 바뀌지 않는다**: coffeereview 리뷰 산문에는 별칭을 끈다.
- 앞선 결정: [ADR 0018](0018-open-tag-cooccurrence.md)이 찾은 틈 — 매퍼가 SCA 휠의 영어 단수 이름("caramelized", "nutty",
  "citrus fruit", "musty/earthy")만 알아서 "chocolate caramel wet nut"은 초콜릿만, "earthy, spicy"는 아무것도 못 읽었다. Zenodo
  패널 정답도 같은 매퍼로 읽어 "caramel"(189회)·"nuts"(148회)가 정답에서 빠졌다.

## 결정
1. **별칭 표 하나** — `pipeline/enrich.py` `EN_TAG_ALIASES`(96개)와 휠 이름의 복수형(`_plural_forms`: berry → berries, peach →
   peaches, hazelnut → hazelnuts). 대상이 어휘에 있는 태그일 때만 쓰고, 휠 이름이 별칭보다 우선한다. `rule_tags`가 이 표를 읽고
   `text_tags`는 `rule_tags`를 부르므로 한 곳에서 관리된다. 주요 매핑: caramel·toffee·butterscotch → 캐러멜(caramelized),
   nut·nuts·walnut·pecan·cashew → 견과(nutty), almond → almonds, chocolatey·cacao → 초콜릿·코코아, currant·cranberry → 베리,
   citrus·citrusy·bergamot → 시트러스, tangerine·mandarin → 오렌지, stone fruit·apricot·nectarine → 복숭아(ADR 0017의 살구 → 복숭아와
   같은 선택), tropical·mango·lychee·plum·melon → 기타 과일, flowers·blossom·lavender·hibiscus → 꽃향, earl grey → 홍차, herbal·
   lemongrass → 허브, wine → 와인, smoke → 스모키, wood·cedar·sandalwood → 나무, earthy·earth → 흙내, spice·spicy → 브라운 스파이스.
2. **최장 일치·단어 경계 유지** — 긴 표현부터 맞추고, 앞서 잡힌 태그를 포함하는 태그("blueberries" 뒤의 berry)와 앞서 잡힌 구간 안의
   표현("orange blossom" 안의 orange)은 건너뛴다. 단어 경계 때문에 "coconut"·"walnut" 안의 "nut"은 걸리지 않는다(walnut은 스스로
   견과로 매핑). 별칭을 끄면(`en_aliases={}`) 이전 규칙과 바이트 단위로 같다 — 전체 리뷰 7,401건에서 차이 0, 테스트로 고정.
3. **coffeereview 산문에는 끔** (`EN_ALIASES_OFF_SOURCES`) — 긴 리뷰 산문에서 "wood"·"earth"·"spice"·"nut"은 노트가 아닌 경우가
   많고, 켜면 coffeereview 7,393건 중 6,410건의 라벨이 바뀌어(원두당 2.9 → 4.5개) 전체판 태그 모델의 학습 라벨이 움직인다. 끈 뒤
   다시 돌린 드라이런: coffeereview 변경 **0건**. 전체판에서 바뀌는 것은 손님 입력 단서(에코)뿐이다.
4. **로컬 coffee_open 재적재** — 오픈 원두 라벨이 크게 바뀌어(아래) 저장소 경로 그대로 다시 만들었다:
   `python -m pipeline run --only enrich --limit 0`(LLM 호출 0) → `--only embed`(태그가 임베딩 텍스트에 들어가므로 바뀐 163개만
   다시 임베딩, 요청 6회) → `bash scripts/competition/build_open_db.sh`. 전체 `coffee` DB는 적재하지 않았다. 이어서 공기 표
   (`scripts/build_tag_cooc.py`)와 특징 모델(`scripts/train_feature_model.py`, `config/feature_model_open.json`의 유일한 작성자,
   레시피 불변)을 새 DB로 다시 만들고 영향받는 평가를 전부 다시 돌렸다.

## 측정 — [`scripts/eval_en_aliases.py`](../../scripts/eval_en_aliases.py) → [`phase8_en_aliases.json`](../../data/eval/open/phase8_en_aliases.json)
"이전"은 같은 코드에서 별칭 표만 비운 것이다(= 이전 매퍼).

### 손님 입력 (런타임 `text_tags`, 영어·한영 혼합 카드 16개 — 문구와 기대 태그를 같은 사람이 써서 낙관적)
| | 문구당 태그 | 태그가 하나라도 읽힌 문구 | 기대 태그 대비 정밀도 · 재현율 |
|---|---|---|---|
| 이전 | 0.75 | 10/16 | 0.833 · 0.263 |
| **이후** | **2.44** | **16/16** | **0.974 · 1.000** |

예: "chocolate caramel wet nut" 초콜릿 → 초콜릿·캐러멜·견과, "earthy, spicy, full body" 없음 → 흙내·브라운 스파이스, "Panama Geisha
orange blossom, peachy, earl grey" 오렌지(오탐) → 꽃향·복숭아·홍차, "Honduras decaf mountain water caramel" 없음 → 캐러멜,
"브라질 세라도 내추럴 caramel nuts" 없음 → 캐러멜·견과. 틀린 것 하나: "Colombia Huila honey process, ..."의 honey(가공 방식)는 이전에도
꿀로 읽혔다(별칭과 무관한 기존 오탐).

### 오픈 원두 저장 라벨 — 재적재 전 드라이런 (규칙 + 캐시된 LLM 출력, 쓰기 없음)
| 출처 | 원두 | 라벨 바뀜 | 얻은 / 잃은 태그 | 태그 원두당(태그 있는 원두 기준 아님) |
|---|---|---|---|---|
| shopify_gauged | 199 | **95** | +127 / −20 | 1.52 → 2.06 |
| roasters_kr | 209 | 18 (영어) + 49 (ADR 0017 한국어 별칭, 적재된 적 없던 것) = **67** | +24 / −4 (영어분) | 1.76 → 1.85 (DB 기준 1.45 → 1.85) |
| shopify(블루보틀 코리아) · roasterdb · cqi | 9 · 100 · 1,546 | 0 | — | — |
| (참고) coffeereview_kaggle, 산문에서 별칭 끔 | 7,393 | 0 | — | 2.91 그대로 |

얻은 태그는 캐러멜 31 · 견과 14 · 기타 과일 14 · 베리 13 · 복숭아 8 · 아몬드 8 · 흙내 5 순. "잃은" 태그는 규칙이 이전에 아무것도 못 읽어
LLM 태그를 쓰던 원두에서 규칙이 이제 무언가를 읽어 LLM 태그를 대신한 경우다(예: "Sandalwood, Nuts, Earthy Tastes" LLM 나무 →
규칙 견과·흙내·나무). 같은 이유로 두 원두는 LLM이 채우던 단맛 값이 빠졌다. 태그 있는 원두 수는 그대로(147 · 121).

### Zenodo 패널 — 정답 쪽(평가 전용)과 예측 쪽, 재적재 전 2×2 (오픈판 분석 경로, 노트 포함 카드 전체)
정답 태그가 샘플당 3.78 → **7.45개**, 정답이 있는 샘플 192 → 196개. 예측기 태그(손님 단어 에코 전 — `phase2_zenodo_external.json`이
채점하는 것)의 정밀도 · 재현율 · F1 · 대분류 F1:

| 예측 \ 정답 | 이전 매퍼 정답 | 새 매퍼 정답 |
|---|---|---|
| 이전 매퍼 예측 | 0.359 · 0.274 · 0.311 · 0.564 | 0.471 · 0.185 · 0.266 · 0.560 |
| 새 매퍼 예측 | 0.357 · 0.273 · 0.309 · 0.566 | 0.460 · 0.182 · 0.261 · 0.559 |

정밀도는 정답이 온전해져서 오르고(0.36 → 0.46), 재현율은 정답이 두 배가 되어 내려간다 — 예측이 나빠진 것이 아니라 이전 정답이
빠뜨린 태그가 많았던 것이다. 에코를 포함해 카드에 보이는 태그(순환이 섞인 참고치)는 이전/이전 0.729 · 0.826 → 새/새 0.960 · 0.634,
샘플당 4.38 → 4.93개. 전체판은 예측이 그대로이고 정답만 바뀐다(재적재 뒤 파일 기준 0.452 · 0.429 · 0.440 · 0.682 → 0.575 · 0.281 ·
0.377 · 0.633).

## 재적재 뒤 — 영향받은 평가 (이전 → 이후, 모두 로컬 coffee_open)
| 평가 | 이전 | 이후 |
|---|---|---|
| 오픈판 산미 Zenodo(196) ±1 · MAE · 순위상관 | 81.6% · 0.63 · 0.62 | **85.7% · 0.54 · 0.71** |
| 오픈판 바디 Zenodo ±1 · 순위상관 (항상 3: 98.5%) | 78.8% · −0.03 | 78.2% · −0.01 |
| 오픈판 단맛 Zenodo, 근거 없으면 기권: 답한 비율 · ±1 · 순위상관 | 88.8% · 44.2% · −0.26 | 85.2% · **35.3%** · −0.29 |
| 산미 E1(로스터리 단위 CV, 탑재 레시피 +B) ±1 · MAE | 0.695 · 0.771 | 0.707 · 0.738 |
| 특징 모델 재학습 게이트(`phase3` ridge ±1: 산미/바디/단맛) | 0.622 / 0.781 / 0.569 | 0.695 / 0.781 / 0.597 |
| 단맛 E1, 근거 없으면 기권: 답한 비율 · ±1 · MAE | 0.514 · 0.595 · 0.968 | 0.486 · 0.657 · 0.833 |
| 노트 없는 입력 태그(ADR 0016) E1 F1 · 대분류 / E2 | 0.196 · 0.604 / 0.185 · 0.554 (n=110) | 0.265 · 0.657 / 0.294 · 0.629 (n=112) |
| 노트 한 단어 입력 채우기(ADR 0017) E1 대분류 · 정밀도 이전→탑재 | 0.315 → 0.507 · 0.180 → 0.141 (n=137) | 0.316 → 0.509 · 0.134 → 0.128 (n=211) |
| 같은 것 E2 | 0.316 → 0.364 · 0.341 → 0.293 (n=53) | 0.436 → 0.471 · 0.398 → 0.385 (n=144) |
| 공기 채우기(ADR 0018) 태그 1개 이하 E1 / E2 | 8.8% → 6.6% / 32.1% → 32.1% | 2.4% → 0.9% / 4.9% → 4.2% |
| 오픈 LOO(CQI 고정 200) 산미 ±1 | 0.525 | 0.51 |
| 모의 사용자 수렴 MAE (오픈) | 0.7674 → 0.6523 | 0.7403 → 0.6334 |

- 태그 평가의 표본이 바뀌었다(E1 노트 한 단어 137 → 211, E2 53 → 144): 첫 노트 단어가 영어인 원두·패널 카드가 이제 에코를 낸다.
  ADR 0017의 정밀도 비용(−0.039 / −0.048)은 새 정답으로 −0.006 / −0.013이고, ADR 0018이 막았던 "태그 1개뿐" 문제는 E2에서 32% →
  5%로, 대부분 에코가 영어를 읽게 된 덕이다.
- 산미 Zenodo가 오른 것은 패널 카드의 영어 노트가 이제 특징 모델의 노트 대분류 특징으로 들어가서다(이전 설정 파일로도 85.7%).
  단맛 Zenodo ±1이 44% → 35%로 떨어진 것은 재학습 탓이다(같은 입력, 이전 설정 파일로는 43%). 단맛은 원래 패널로 순위가 옮겨 가지
  않는다(순위상관 음수) — 저장소의 재학습 게이트(E1 ridge ±1, 떨어진 속성 없음)는 통과했다.
- LOO·수렴은 163개 원두의 임베딩이 바뀐 이웃 이동이다(산미 ±1 −1.5%p, ADR 0006이 말하는 잡음 폭 근처).
- 전체판: `coffee` DB·`config/tag_model.json`·`attr_model.json`은 그대로, Zenodo 전체판 수치 중 속성은 동일.

## 기각·보류
- **coffeereview 산문에도 별칭 켜기**: 전체판 라벨 6,410건이 바뀌고 산문 오탐("wood-fired" 류) 위험 — 전체판 불변 요구로 기각.
- **"honey process"를 꿀에서 빼기, "red fruit"·"fig"·"dates"·"nougat" 매핑**: 뜻이 갈리거나 기존 동작 변경이라 이번 범위 밖.
- **후속(결정 안 바꿈)**: (1) 새 정답으로 다시 재면 ADR 0018의 완화된 공기 게이트(몫 ≥ 0.2, 3개까지)가 두 패널 모두 좋아진다 —
  E1 정밀도 0.128 → 0.135 · 대분류 0.509 → 0.533, E2 0.385 → 0.391 · 0.471 → 0.513(더한 태그 정밀도 19% / 45%). 같은 표본으로
  고른 격자라 별도 ADR에서. (2) 재적재 뒤 `ablate_open_labels.py`가 바디(+A+B+C, E1 +0.078)와 단맛(+Bf, +0.042) 레시피 교체 후보를
  표시하고, `eval_open_v3.py`의 바디 후보 `+A wA=1`도 E1 0.781 vs 이웃 0.719 · E2 0.827 vs 0.782로 ADR 0016 규칙을 넘는다 —
  `SHIPPED_RECIPES`는 ADR로만 바꾸므로 그대로 둠.

## 운영 반영
- 코드(별칭)와 `config/`(공기 표·특징 모델)는 배포 이미지로, 오픈 DB 라벨·임베딩은 로컬 coffee_open을 `MODE=catalog`로 Neon에
  옮겨야 운영에 들어간다. 공유 캐시 `data/enriched/coffees.jsonl`·임베딩 캐시에는 오픈 출처 162개(전체 DB에도 있는 원두)의 새 라벨이
  들어 있어, 다음 전체판 적재·주간 갱신이 이 원두들의 라벨을 바꾼다(coffeereview는 불변).
- `data/raw/open_tag_eval/v3_rows.pkl`(eval_open_v3의 DB 행 캐시)은 재적재 전 행이라 옮겨 두고 다시 만들었다 — DB를 다시 적재하면
  이 캐시를 지워야 한다.

## 재현
```
uv run python scripts/eval_en_aliases.py                    # 드라이런·손님 입력·Zenodo 2×2 (재적재 전에 실행) → phase8_en_aliases.json
uv run python -m pipeline run --only enrich --limit 0       # 규칙만 다시, LLM 호출 0 (공유 enriched 캐시)
uv run python -m pipeline run --only embed                  # 바뀐 163개만 다시 임베딩
bash scripts/competition/build_open_db.sh                   # 로컬 coffee_open만 적재 + violations/loo/coverage/convergence
uv run python scripts/build_tag_cooc.py && uv run python scripts/train_feature_model.py
uv run python scripts/ablate_open_labels.py && uv run python scripts/eval_open_v3.py     # v3_rows.pkl 지운 뒤
uv run python scripts/eval_open_tags.py && uv run python scripts/eval_open_tag_fill.py && uv run python scripts/eval_open_tag_cooc.py
uv run python scripts/eval_zenodo_panel.py
```
