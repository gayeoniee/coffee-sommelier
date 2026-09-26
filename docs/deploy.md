# 배포 가이드

무료 등급 3곳에 나눠 올린다. 브라우저는 Vercel만 보고, Vercel의 `/api/*` 라우트 핸들러가 Render의 FastAPI로 쿠키·SSE를 그대로 중계한다(같은 출처라 교차 사이트 쿠키·CORS 문제가 없다).

```
브라우저 ──https──▶ Vercel (Next.js, 함수 리전 sin1)
                     └─ /api/* ──▶ Render (FastAPI 도커, singapore, 무료)
                                     ├─▶ Neon (Postgres 17 + pgvector, aws-ap-southeast-1, 무료)
                                     └─▶ NVIDIA API (설명·파싱·임베딩)
```

세 곳 모두 싱가포르라 서버 간 왕복이 짧고, 한국에서 체감 지연은 Vercel↔브라우저 한 구간이다.

## 0. 준비 (한 번)

- 이 브랜치가 `main`에 머지·푸시돼 있어야 한다. Render 블루프린트(`render.yaml`)와 자동 배포가 GitHub의 `main`을 읽는다.
- 로컬 DB가 떠 있고 데이터가 최신이어야 한다: `docker compose up -d db`. 이 DB를 Neon으로 복사한다.
- 임베딩: 운영에는 Ollama가 없다. `config/models.yaml`의 `tasks.embed`가 NVIDIA 모델(`nvidia/nemotron-3-embed-1b`, 1024차원)을 가리키고, 로컬 DB의 `coffees.embedding`도 그 모델로 다시 만든 뒤 옮겨야 원두 분석의 유사 원두 검색이 맞게 동작한다. (설명·파싱의 Ollama 폴백은 운영에서 즉시 실패하고 템플릿 설명으로 넘어가므로 문제없다.)
- `.env`에 `NVIDIA_API_KEY`가 있어야 한다.

## 1. 가장 짧은 길: 로그인 3번 + 스크립트 1번

계정이 없으면 각 로그인 화면에서 **GitHub로 가입**하면 된다(모두 무료, 카드 불필요).

```bash
npx neonctl auth                 # 브라우저가 열림 → GitHub로 로그인
npx vercel login                 # 브라우저가 열림 → Continue with GitHub
# Render: https://dashboard.render.com 에 GitHub로 가입 → Account Settings → API Keys → Create API Key
export RENDER_API_KEY=rnd_...    # (선택) 없으면 스크립트가 블루프린트 링크를 띄워 준다

bash scripts/deploy/deploy_all.sh
```

스크립트가 하는 일:
1. Neon 프로젝트 `coffee-sommelier`(싱가포르, PG 17)를 찾거나 만들고 direct 연결 문자열을 받는다.
2. `scripts/deploy/migrate_to_neon.sh`로 로컬 DB를 복사한다(이미 데이터가 있으면 건너뜀).
3. Render 서비스 `coffee-sommelier-api`를 만들거나(API 키가 있을 때) 블루프린트 링크를 보여 주고 URL을 입력받은 뒤, `/health`가 응답할 때까지 기다린다(첫 도커 빌드 5분 안팎).
4. Vercel 프로젝트 `coffee-sommelier`(루트 디렉터리 `web`)에 `API_URL`을 넣고 `--prod` 배포한다.
5. `https://<vercel 주소>/api/health`로 Vercel→Render 중계를 확인하고 주소를 출력한다.

다시 실행해도 같은 이름의 리소스를 재사용하므로 안전하다. 옵션: `KEEP_WARM=1`(아래 5절 핑 켜기), `SKIP_MIGRATE=1`, `NEON_DATABASE_URL=...`(Neon 단계 건너뜀), `RENDER_URL=...`(Render 단계 건너뜀), `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`(트레이싱), `YES=1`(확인 질문 생략).

## 2. 단계별로 직접 하기

### (a) Neon

```bash
npx neonctl auth
npx neonctl projects create --name coffee-sommelier --region-id aws-ap-southeast-1 --pg-version 17 --set-context
npx neonctl connection-string          # direct 연결 문자열 (…neon.tech/neondb?sslmode=require…)
```

`-pooler`가 붙지 않은 **direct** 문자열을 쓴다. `pg_restore`는 세션이 필요하고, API 쪽 커넥션 풀도 최대 5개라 풀러가 필요 없다.

### (b) 로컬 DB → Neon

```bash
NEON_DATABASE_URL='postgresql://...' bash scripts/deploy/migrate_to_neon.sh
```

- `pg_dump`/`pg_restore`/`psql`은 로컬 pgvector 컨테이너 **안에서** 돈다 → 호스트에 Postgres 도구가 필요 없다.
- `vector` 확장 생성 → 스키마 전체 + 카탈로그 데이터 복원(한 트랜잭션, 실패하면 전부 롤백) → 테이블별 행 수 대조 → `ANALYZE`.
- 복사하지 않는 데이터: 사용자 테이블(`users`, `taste_profiles`, `tastings`, `profile_history`: 운영은 빈 상태로 시작), 파이프라인 전용(`reviews`, `enrich_log`: API가 읽지 않음, `INCLUDE_REVIEWS=1`이면 포함).
- 크기: 로컬 대역 컨테이너로 실측 **131 MB**(그중 HNSW 인덱스가 대부분), 약 30초. Neon 무료 한도 0.5 GB 안이다.
- 대상에 테이블이 이미 있으면 멈춘다. 처음부터 다시 옮기려면 `RESET=1`(운영 사용자 데이터까지 지워짐). 카탈로그만 갱신하려면 로컬에서 `DATABASE_URL=<neon> uv run python -m pipeline run --only load`(upsert).

### (c) Render (백엔드)

1. 브라우저에서 https://render.com/deploy?repo=https://github.com/gayeoniee/wine-sommelier_rag 를 연다 → GitHub로 로그인.
2. 블루프린트가 `render.yaml`을 읽어 무료 도커 웹 서비스 `coffee-sommelier-api`(singapore, 헬스체크 `/health`)를 보여 준다. 입력칸:
   - `DATABASE_URL` = (a)의 Neon 연결 문자열
   - `NVIDIA_API_KEY` = `.env`의 값
   - `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` = 비워 두면 트레이싱 꺼짐
3. **Deploy Blueprint** → 빌드가 끝나면 `https://coffee-sommelier-api-xxxx.onrender.com` 주소가 나온다. `/health`가 `{"ok":true}`면 성공.

이후 `main`에 백엔드 경로(`app/`, `pipeline/`, `config/`, `pyproject.toml`, `uv.lock`, `Dockerfile`)가 바뀌면 자동 재배포된다. `web/`만 바뀐 푸시는 빌드하지 않는다.

### (d) Vercel (프론트)

```bash
npx vercel login
npx vercel project add coffee-sommelier
npx vercel project update coffee-sommelier --framework nextjs --root-directory web --yes
npx vercel link --yes --project coffee-sommelier                    # 저장소 루트에서
npx vercel env add API_URL production --value https://coffee-sommelier-api-xxxx.onrender.com --yes
npx vercel deploy --prod
```

- 프로젝트 루트 디렉터리를 `web`으로 두고 저장소 루트에서 링크한다. 그래서 CLI 배포(`.vercelignore`로 `web/`만 업로드)와 GitHub 연동(푸시 → 자동 배포)이 같은 설정으로 동작한다. `link --yes`가 GitHub 저장소 연결까지 시도한다.
- `web/vercel.json`은 함수 리전만 `sin1`(싱가포르)로 고정한다. 기본값(미국 동부)이면 Render까지 태평양을 두 번 건넌다.
- 프록시 라우트는 `maxDuration = 60`(초)이다. Hobby 한도 안이며, Render가 잠에서 깨는 30~60초와 설명 스트리밍(12초 이내)을 모두 덮는다.
- `API_URL` 끝의 `/`는 있어도 된다(프록시가 정리함).

## 3. 환경변수

| 위치 | 이름 | 값 | 필수 |
|---|---|---|---|
| Render | `DATABASE_URL` | Neon direct 연결 문자열 | ✔ |
| Render | `NVIDIA_API_KEY` | build.nvidia.com 키 | ✔ |
| Render | `COOKIE_SECURE` | `true` (블루프린트가 넣음) | |
| Render | `COOKIE_SAMESITE` | `lax` (블루프린트가 넣음) | |
| Render | `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Langfuse Cloud 키, 비우면 끔 | |
| Render | `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` (US 프로젝트는 `https://us.cloud.langfuse.com`) | |
| Render | `ALLOWED_ORIGINS` | 필요 없음 — 브라우저가 API를 직접 부르지 않는다 | |
| Vercel | `API_URL` | Render 서비스 주소 | ✔ |

`PORT`는 Render가 넣고 컨테이너가 그대로 쓴다. 쿠키는 Vercel 도메인(HTTPS)에서 1st-party로 설정되므로 `Secure; SameSite=Lax`가 맞다.

## 4. 백엔드 이미지

`Dockerfile`: `python:3.12-slim` + uv, API 런타임 의존성만 설치한다(파이프라인 전용인 pandas·numpy·bs4·lxml·kaggle은 `pyproject.toml`의 `pipeline` 그룹으로 분리, 로컬 `uv sync`는 기존처럼 전부 설치). 비루트 사용자, uvicorn 워커 1개.

로컬 실측(로컬 DB·Neon 대역 DB 각각에 연결해 `/health`, `/session`, `/brands`, `/recommend`, `/analyze` 확인):

| 항목 | 값 |
|---|---|
| 이미지 크기 | 356 MB (그중 venv 138 MB) |
| 기동 직후 메모리 | 약 71 MiB |
| 추천·분석 스트리밍 후 | 약 75 MiB |
| Render 무료 한도 | 512 MB |

로컬에서 돌려 보기:

```bash
docker build -t coffee-api .
docker run --rm -p 8000:10000 -e PORT=10000 -e NVIDIA_API_KEY=... \
  -e DATABASE_URL=postgresql://coffee:coffee@host.docker.internal:5432/coffee coffee-api
curl localhost:8000/health
```

## 5. 콜드 스타트와 깨우기 핑

Render 무료 서비스는 15분 동안 요청이 없으면 잠들고, 다음 요청에서 30~60초 걸려 깨어난다. 웹앱은 이미 대비돼 있다: 첫 화면의 `WakeGate`가 `/session`을 재시도하며 "서버를 깨우는 중이에요… (처음엔 30초쯤 걸려요)"를 보여 준다.

더 빠르게 하고 싶으면 `.github/workflows/keep-warm.yml`을 켠다. 한국 시간 08~24시에 14분마다 `/health`를 찔러 깨어 있게 한다.

```bash
gh variable set KEEP_WARM_URL --body https://coffee-sommelier-api-xxxx.onrender.com/health   # 켜기
gh variable delete KEEP_WARM_URL                                                            # 끄기
```

- 하루 16시간 × 30일 ≈ 480시간. Render 무료 인스턴스 시간(월 750시간, 서비스 1개)의 안이다.
- `/health`는 DB를 건드리지 않으므로 Neon은 그대로 유휴 시 자동 정지한다(깨어날 때 1초 미만).
- GitHub 예약 실행은 몇 분씩 늦을 수 있고, 저장소에 60일간 활동이 없으면 멈춘다.

## 6. 제출본(오픈 데이터판)

공모전 심사용으로 `coffeereview` 소스가 전혀 없는 DB로 도는 별도 배포다. 리소스는 전체판과 나란히 존재하고 서로 덮어쓰지 않는다.

| | 전체판 | 제출본(오픈 데이터판) |
|---|---|---|
| Neon | 프로젝트 `coffee-sommelier`, DB `neondb` | 같은 프로젝트, DB `coffee_open` |
| Render | `coffee-sommelier-api` | `coffee-sommelier-open-api` |
| Vercel | `coffee-sommelier` | `coffee-sommelier-open` |
| `DATA_VARIANT`(Render) | `full`(기본) | `open` |
| `NEXT_PUBLIC_VARIANT`(Vercel) | 없음 | `open` — 화면 맨 위에 "공모전 제출본 · 오픈 데이터 + 국내 로스터리 사실정보 (coffeereview 미포함)" 배너 |

준비: 로컬에 오픈 데이터판 DB `coffee_open`이 있어야 한다(`scripts/competition/build_open_db.sh`로 만든다 — 로컬 도커 컨테이너 안의 DB이지, Neon이 아니다). 그 DB에는 `coffeereview_kaggle` 소스가 한 행도 없다.

배포:

```bash
VARIANT=open bash scripts/deploy/deploy_all.sh
```

`deploy_all.sh`가 하는 일(전체판과 다른 부분만):
1. Neon: 프로젝트는 재사용하고, DB `coffee_open`이 없으면 `neonctl databases create`로 만든 뒤 연결 문자열의 DB 이름 부분만 바꿔 쓴다.
2. 마이그레이션: 로컬 컨테이너의 `coffee_open` DB(`SRC_DB=coffee_open`, 오버라이드 가능)를 방금 만든 Neon `coffee_open`으로 옮긴다.
3. Render: 서비스 `coffee-sommelier-open-api`를 만들거나 갱신하고 env `DATA_VARIANT=open`을 넣는다.
4. Vercel: 프로젝트 `coffee-sommelier-open`에 `API_URL`(오픈 백엔드)과 `NEXT_PUBLIC_VARIANT=open`을 넣고 배포한다.

확인: `https://<제출본 웹 주소>/api/health` → `{"ok": true, "variant": "open", "coffees": <오픈 DB 원두 수>}`.

CI(`deploy` job)는 push마다 `coffee-sommelier-api`와 `coffee-sommelier-open-api` 두 서비스 모두에 배포를 트리거한다. 아직 만들지 않은 서비스(제출본을 처음 배포하기 전)는 오류 없이 건너뛴다. Render 블루프린트(`render.yaml`)로 처음부터 만들 때도 두 서비스가 함께 나열되므로, 만들 서비스를 골라 각각 `DATABASE_URL`(제출본은 Neon `coffee_open` 연결 문자열)과 `NVIDIA_API_KEY`를 채운다.

## 7. CI

`.github/workflows/ci.yml` (푸시·PR):
- `backend`: pgvector 서비스 컨테이너를 띄우고 `uv run pytest -q` (DB 테스트 포함, `coffee_test`는 픽스처가 만든다).
- `image`: 도커 이미지를 빌드하고 pandas 없이 `app.api`가 임포트되는지 확인.
- `web`: `npm ci && npm test && npm run lint && npx tsc --noEmit && npm run build`.
- `deploy`(`main` push만): `coffee-sommelier-api`·`coffee-sommelier-open-api` 두 서비스 모두에 Render 배포를 트리거한다(둘 중 아직 없는 서비스는 건너뜀).

## 8. 문제 해결

| 증상 | 확인 |
|---|---|
| 화면이 "서버를 깨우는 중"에서 오래 멈춤 | Render 대시보드 Logs. 첫 배포면 빌드 중일 수 있다. `https://<render>/health` 직접 열어 보기 |
| `/api/*`가 502 "서버에 연결할 수 없어요" | Vercel의 `API_URL` 오타·누락. 바꾼 뒤엔 `npx vercel deploy --prod`로 재배포해야 반영된다 |
| 401 "세션이 없어요" 반복 | 브라우저가 쿠키를 막는지(시크릿 모드 서드파티 설정). 쿠키는 Vercel 도메인에 `Secure`로 설정된다 |
| Render 로그에 DB 연결 오류 | `DATABASE_URL`이 direct 문자열인지, `sslmode=require`가 붙었는지 |
| 원두 분석 결과가 모두 "예측 신뢰도 낮음" | 임베딩 설정(0절). `config/models.yaml`의 embed가 ollama면 운영에서 임베딩이 실패한다 |
| Neon 용량 경고 | `reviews`·`enrich_log`를 넣지 않았는지(`INCLUDE_REVIEWS` 미사용), 대시보드에서 브랜치·히스토리 보존 기간 확인 |

## 8. 운영 통계 (Render 로그)

`app/telemetry.py`가 추천·분석 스트림이 끝날 때마다 JSON 한 줄(`evt`, `cards`, `ms_total`, `ms_first_token`, `fallback`, `error`, `aborted` — 클라이언트가 스트림 도중 끊으면 true)을 `create_app`이 붙인 전용 stdout 핸들러로 남긴다(uvicorn 기본 설정에선 루트 로거가 WARNING이라 핸들러 없이는 INFO 줄이 버려진다). `scripts/ops/prod_stats.py`가 Render 로그 API로 최근 N시간의 로그를 받아 그 줄만 골라 폴백 비율·첫 토큰 p50/p95·에러율을 집계한다.

```bash
uv run python scripts/ops/prod_stats.py --hours 24
```

- `RENDER_API_KEY`를 환경 변수 또는 레포 루트 `.env`에서 읽는다(Render 대시보드 → Account Settings → API Keys).
- 서비스 id는 `/v1/services?name=coffee-sommelier-api`로, owner id는 `/v1/owners`로 조회한 뒤 `/v1/logs`를 `hasMore`가 꺼질 때까지 페이지네이션한다.
- `--service-name`으로 다른 서비스 이름을 지정할 수 있다(기본 `coffee-sommelier-api`).
- 결과는 표로 출력되고, `data/eval/prod_stats_<YYYY-MM-DD>.json`에도 저장된다.
- 텔레메트리 로그 줄이 아직 없거나(배포 직후) 조회 구간에 요청이 없으면 `requests: 0`으로 정상 종료한다.
