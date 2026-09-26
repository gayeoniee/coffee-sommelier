# ADR 0006 — 이웃 검색 동점을 id로 결정

- 상태: 채택 (2026-09-27)

## 맥락
`Repo.neighbors`는 `ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s`로 pgvector HNSW 인덱스를 태운 뒤, 파이썬에서
`key=lambda r: -r["sim"]`로 다시 정렬한다. 코사인 거리가 완전히 같은 원두 쌍(임베딩 텍스트가 산지·지역·농장·가공까지 같아서
생기는 "쌍둥이" — CQI 1,546개 중 744개가 여기 해당)이 많아, 동점 안에서 어느 쪽이 먼저 나오는지는 HNSW 그래프 탐색 순서·행의
물리적 저장 순서에 좌우된다. `TRUNCATE ... RESTART IDENTITY` 후 재적재하면 물리적 순서가 바뀌어 동점 중 뽑히는 원두가 달라지고,
leave-one-out(`app/eval.py::loo_accuracy`) 결과가 **같은 데이터·같은 대상인데도 실행마다 1~3%p** 움직인다(README "2단계 결과"에
이미 기록된 현상). 정렬에 결정적 동점 규칙이 없다는 것이 근본 원인이다.

## 결정
SQL 정렬에 `id`를 2차 키로 추가하고, 파이썬 재정렬 키도 맞춘다.

```sql
ORDER BY embedding <=> %(v)s::vector, id LIMIT %(k)s
```
```python
sorted(rows, key=lambda r: (-r["sim"], r["id"]))
```

## HNSW 인덱스가 유지되는가 — `EXPLAIN (ANALYZE, BUFFERS)` (dev DB `coffee`, 9,155건)
`id` 추가 전후로 dev DB에서 실제 쿼리(`SET LOCAL hnsw.iterative_scan = relaxed_order; hnsw.ef_search = 200;`,
실제 원두 하나의 1024차원 임베딩을 파라미터로 바인딩, 동점 노이즈를 재현하지 않는 일반적인 벡터라 순수 성능 비교용)를 돌렸다.

**정렬 키에 `id` 추가 전:**
```
Limit  (cost=2064.15..2150.68 rows=10 width=108) (actual time=6.016..6.224 rows=10 loops=1)
  Buffers: shared hit=1394
  ->  Index Scan using coffees_embedding_idx on coffees  (cost=2064.15..81280.88 rows=9155 width=108)
        (actual time=6.015..6.221 rows=10 loops=1)
Planning Time: 1.214 ms
Execution Time: 6.295 ms
```

**정렬 키에 `id` 추가 후:**
```
Limit  (cost=2072.81..2159.78 rows=10 width=108) (actual time=1.515..1.517 rows=10 loops=1)
  Buffers: shared hit=1380
  ->  Incremental Sort  (cost=2072.81..81692.85 rows=9155 width=108) (actual time=1.514..1.515 rows=10 loops=1)
        Sort Key: ((embedding <=> '[...]'::vector)), id
        Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 26kB  Peak Memory: 26kB
        ->  Index Scan using coffees_embedding_idx on coffees  (cost=2064.15..81280.88 rows=9155 width=108)
              (actual time=1.398..1.495 rows=11 loops=1)
Planning Time: 0.180 ms
Execution Time: 1.535 ms
```

**HNSW 인덱스는 그대로 유지된다** — 두 계획 모두 `Index Scan using coffees_embedding_idx`가 최하단에 남아 있다. `id`를
2차 정렬 키로 추가하면 플래너가 `Incremental Sort` 노드 하나를 그 위에 얹는데, pgvector가 이미 거리순으로 내보낸 결과를 "그룹
(동점 구간)별로만" 재정렬하는 저비용 연산이라 `Full-sort Groups: 1`, `quicksort`, 26kB로 사실상 공짜다. 브리핑에서 예상한
대안(k+20을 받아 파이썬에서 `(-sim, id)`로 정렬 후 k개)은 **필요 없었다** — SQL 정렬만으로 인덱스와 결정성을 둘 다 얻는다.

## 지연 측정 (`Repo.neighbors`가 실행하는 것과 같은 쿼리, dev DB, 10회, 중앙값)
`.superpowers/sdd/2026-09-27-ai-depth/`(스크래치 스크립트로 측정, 커밋 안 함) 동일 커넥션 풀·`SET LOCAL` 설정으로 10회씩
실행한 결과:

| | id 추가 전 | id 추가 후 |
|---|---|---|
| 지연 중앙값 (10회) | 47.974ms | 48.329ms |

차이(+0.355ms, +0.7%)는 측정 잡음 안이다 — `Incremental Sort`가 이미 정렬된 스트림 위에서 도는 저비용 연산이라는 것과 일치한다.

## LOO 재현성 확인 (`app/eval.py::loo_repro`)
`loo_accuracy`를 같은 인자(`n=200, seed=42`)로 두 번 돌려 결과 JSON(`json.dumps(..., sort_keys=True)`)의 sha256을 비교한다.

```
uv run python -m app.eval loo_repro   # → data/eval/phase2_loo_repro.json
```
```json
{
  "runs": 2,
  "identical": true,
  "sha256": ["f6b84417623aa2b696dfa1b81f30afa6c482e1132767001ea9dfc8104bab9650",
             "f6b84417623aa2b696dfa1b81f30afa6c482e1132767001ea9dfc8104bab9650"]
}
```
동점을 id로 고정한 뒤에는 같은 프로세스 안에서 반복 실행한 결과가 바이트 단위로 동일하다. (재적재로 물리적 행 순서 자체가
바뀌는 경우까지 보장하지는 않는다 — id는 고정이지만 그 사실은 이미 결정적이므로, 재적재해도 이제는 항상 같은 결과가 나온다.)

## 부작용 — 기존 LOO/compare3 수치 재측정
동점 재정렬 규칙이 바뀌었으니(이전: 물리적 행 순서에 의존 → 이후: id 오름차순), 동점이 많은 이웃 10개 구성 자체가 살짝 바뀐다.
`uv run python -m app.eval loo loo_open compare3 loo_repro`로 다시 측정한 값(모두 이 잡음 범위, README에 반영):

| 평가 | 이전 | 이후 |
|---|---|---|
| `loo` 바디 ±1 이내 (n=200) | 0.635 | 0.64 |
| `loo_open` 바디 ±1 이내 (n=198) | 0.5808 | 0.6061 |
| `compare3` 산미 ±1 이내 (전체/오픈/오픈+로스터리) | 0.505 / 0.49 / 0.49 | 0.495 / 0.495 / 0.495 |
| `compare3` 바디 ±1 이내 | 0.575 / 0.565 / 0.565 | 0.595 / 0.595 / 0.595 |

`loo`·`loo_open`의 산미 ±1은 변하지 않았다(0.735, 0.5202). `compare3`는 오히려 세 판의 수치가 완전히 같아졌다 — 동점 규칙이
안정되기 전에는 판마다 물리적 순서가 조금씩 달라 우연히 값이 갈렸던 것이고, 지금은 이웃 후보 풀만 다르고 정렬 규칙은 같아서
차이가 사라졌다.

## 결정하지 않은 것
- `hnsw.ef_search`, `hnsw.iterative_scan` 설정은 바꾸지 않았다 — 이번 변경과 무관.
- `random_coffee_ids_for_loo`는 이미 `ORDER BY id` + 시드 샘플이라 그대로 둔다(변경 불필요).
