# 포트폴리오 마감 — PWA·데모 GIF·아키텍처·설계 결정·정리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 읽는 사람이 3분 안에 무엇·어떻게·왜·얼마나 믿을 수 있나를 알 수 있게 README·문서·PWA 설치 경험·데모 GIF를 마감한다. 새 기능 없음.

**Architecture:** 웹은 정적 자산(아이콘·`sw.js`·오프라인 페이지)과 등록 코드 몇 줄. 문서는 Mermaid와 마크다운. GIF는 Playwright 프레임 + 순수 JS GIF 인코더.

**Tech Stack:** Next.js 16, Vitest, Playwright, `gifenc`(dev), Pillow(파이썬 dev, 아이콘 생성), Mermaid, Python 3.12.

**Spec:** `docs/superpowers/specs/2026-09-27-portfolio-polish-design.md`

## Global Constraints

- 새 런타임 의존성 금지(웹: 서비스 워커는 수동 작성; GIF·아이콘 도구는 dev 전용).
- 색·레이아웃은 유지. README 기존 문장은 삭제하지 않고 순서·제목만 정리(수치는 `data/eval/*.json`과 일치해야 함).
- `/api/*`는 절대 서비스 워커가 캐시하지 않는다.
- 커밋 메시지 한국어 conventional + 세션 트레일러. `cd web && npm test && npm run lint && npx tsc --noEmit && npm run build`, `uv run pytest -q` 통과 후 커밋.

## Review Focus

- **서비스 워커가 API 응답·SSE를 가로챔** → 라우팅 순수 함수 테스트(`/api/` 통과, navigation만 처리)(Task 1).
- **오래된 앱 셸 캐시가 새 배포 뒤 남음** → 캐시 이름에 버전 상수, activate에서 이전 캐시 삭제 + `skipWaiting`/`clients.claim`; 테스트(Task 1).
- **README 수치가 JSON과 어긋남** → Task 3의 `scripts/check_readme_numbers.py`가 README의 핵심 수치(위반 0/N, 메뉴 수, LOO)를 JSON에서 읽어 대조.
- **GIF가 너무 큼/깨짐** → 크기 ≤ 6MB, 프레임 수 상한, 이미지 직접 확인(Task 2).
- **깨진 상대 링크** → `scripts/check_links.py`(Task 3).

## File Structure

```
web/public/icons/{icon-192,icon-512,maskable-512,apple-touch-icon}.png   Task 1 (scripts/make_icons.py 생성)
web/public/sw.js, web/public/sw-routes.js, web/app/offline/page.tsx, web/app/manifest.ts, web/app/layout.tsx, web/components/SwRegister.tsx
web/tests/sw-routes.test.ts
web/scripts/demo-gif.mjs, docs/demo.gif                                    Task 2
docs/architecture.md, docs/design-decisions.md, README.md                  Task 3
scripts/check_links.py, scripts/check_readme_numbers.py, tests/test_docs_checks.py
pipeline/query.py                                                          Task 4 (active 필터)
```

---

### Task 1: PWA — 아이콘, 매니페스트, 서비스 워커, 오프라인 페이지

**Files:**
- Create: `scripts/make_icons.py`(Pillow; dev 의존성 `pillow` 추가), `web/public/icons/*.png`, `web/public/sw.js`, `web/public/sw-routes.js`, `web/app/offline/page.tsx`, `web/components/SwRegister.tsx`, `web/tests/sw-routes.test.ts`
- Modify: `web/app/manifest.ts`, `web/app/layout.tsx`

**Interfaces:**
- Produces: `sw-routes.js` exports(ESM, 브라우저·Vitest 공용) `CACHE = "cs-shell-v1"`, `PRECACHE = ["/", "/onboarding", "/me", "/offline"]`, `classify(request: {url: string, mode: string, method: string}) -> "bypass" | "navigate" | "static"` — `/api/` 또는 GET 아님 → bypass; `mode === "navigate"` → navigate; `/_next/static/` 또는 `/icons/` → static; 그 외 bypass. `sw.js`는 `importScripts` 불가(ESM)이므로 `sw-routes.js` 내용을 `sw.js`가 `import`하는 대신 **빌드 없이** 동작하도록 `sw-routes.js`를 `self.CS_ROUTES = {...}` 형태로도 노출(UMD 스타일 한 파일: `typeof module !== "undefined"`/`self` 분기). navigate: network-first → 실패 시 `caches.match(url)` → `/offline`. static: cache-first. activate: `CACHE`가 아닌 `cs-shell-*` 삭제, `clients.claim()`.
- `manifest.ts`: `icons`(192, 512, maskable 512 with `purpose: "maskable"`), `categories: ["food", "lifestyle"]`, `screenshots`(docs/screenshots 4장은 리포 밖이라 `web/public/screenshots/`로 복사, `form_factor: "narrow"`), `description`.
- `SwRegister.tsx`(client): `useEffect`에서 `process.env.NODE_ENV === "production" && "serviceWorker" in navigator` 일 때 `navigator.serviceWorker.register("/sw.js")`. `layout.tsx`에 `<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />`(metadata `icons.apple`)와 `<SwRegister />`.

- [ ] **Step 1: 실패하는 테스트** — `web/tests/sw-routes.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { CACHE, PRECACHE, classify } from "../public/sw-routes.js";

describe("sw routes", () => {
  it("never touches the API or non-GET", () => {
    expect(classify({ url: "https://x/api/recommend", mode: "cors", method: "POST" })).toBe("bypass");
    expect(classify({ url: "https://x/api/me", mode: "cors", method: "GET" })).toBe("bypass");
    expect(classify({ url: "https://x/", mode: "navigate", method: "POST" })).toBe("bypass");
  });
  it("classifies navigations and static assets", () => {
    expect(classify({ url: "https://x/me", mode: "navigate", method: "GET" })).toBe("navigate");
    expect(classify({ url: "https://x/_next/static/chunks/a.js", mode: "no-cors", method: "GET" })).toBe("static");
    expect(classify({ url: "https://x/icons/icon-192.png", mode: "no-cors", method: "GET" })).toBe("static");
    expect(classify({ url: "https://x/some.json", mode: "cors", method: "GET" })).toBe("bypass");
  });
  it("precache list includes the offline page and the cache is versioned", () => {
    expect(PRECACHE).toContain("/offline");
    expect(CACHE).toMatch(/^cs-shell-v\d+$/);
  });
});
```
- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현** — 위 인터페이스대로. 아이콘: `scripts/make_icons.py`가 크림(#faf6f0) 배경에 에스프레소색(#2b1d14) 둥근 컵 도형 + 로스트색 손잡이(도형만) 생성; maskable은 안전 영역(중앙 80%)에 맞춤. `uv run python scripts/make_icons.py`로 생성해 커밋.
- [ ] **Step 4: 확인** — `cd web && npm test && npm run lint && npx tsc --noEmit && npm run build`; `npm run start` 후 Chrome DevTools 없이 `npx lighthouse http://localhost:3000 --only-categories=pwa --quiet --chrome-flags="--headless"`(설치돼 있지 않으면 `npx -y lighthouse@12`) → 결과 요약을 `docs/screenshots/lighthouse.md`에 기록(점수·통과 항목). 실패 항목이 있으면 고친다(https 요구 항목은 로컬에서 제외되는 점 기록).
- [ ] **Step 5: Commit** — `git commit -m "feat(web): PWA 설치 — 아이콘·매니페스트·서비스 워커(앱 셸)·오프라인 페이지"`

---

### Task 2: 데모 GIF

**Files:**
- Create: `web/scripts/demo-gif.mjs`, `docs/demo.gif`
- Modify: `web/package.json`(dev `gifenc`, script `"demo:gif": "node scripts/demo-gif.mjs"`), `README.md`(상단 GIF)

**Interfaces:**
- Produces: `node scripts/demo-gif.mjs [--base http://localhost:3000] [--out ../docs/demo.gif]` — iPhone 13 뷰포트, 시나리오: `/` → 온보딩 3단계(디카페인만·과일) → 홈 → 할리스 → 카드 3장 + 설명 완료 대기 → 첫 카드 「마셔봤어요」 → 4점 → 저장 → 닫기 → 내 취향. 프레임 400ms 간격, 최대 70프레임, 너비 390px로 축소, gifenc 팔레트 256색. 산출 ≤ 6MB.

- [ ] **Step 1: 구현** — 백엔드(`COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --port 8000`)와 `npm run dev` 필요. 스크립트는 기존 `e2e/smoke.spec.ts`의 셀렉터를 그대로 쓴다.
- [ ] **Step 2: 실행·확인** — `docs/demo.gif` 생성, 크기 확인, Read 도구로 이미지(첫/중간 프레임은 별도 png로도 저장해 확인). README 상단(제목 아래)에 `![데모](docs/demo.gif)`.
- [ ] **Step 3: Commit** — `git commit -m "docs: 데모 GIF(온보딩→추천→기록→내 취향)와 생성 스크립트"`

---

### Task 3: 아키텍처 문서·설계 결정 문서·README 재구성·링크/수치 검사

**Files:**
- Create: `docs/architecture.md`, `docs/design-decisions.md`, `scripts/check_links.py`, `scripts/check_readme_numbers.py`, `tests/test_docs_checks.py`
- Modify: `README.md`

**Interfaces:**
- `scripts/check_links.py [paths...]` → 마크다운의 상대 링크·이미지 경로가 존재하는지, 종료 코드 1이면 깨진 링크 목록.
- `scripts/check_readme_numbers.py` → README에서 정규식으로 뽑은 값(조건 위반 `0/N`, 메뉴 수, LOO 산미/바디 ±1, 커버리지 원두 수)과 `data/eval/phase2_violations.json`·`phase2_coverage.json`·`phase2_loo.json`의 값을 대조; 불일치 시 1.
- `docs/architecture.md`: Mermaid 2장 — (1) 파이프라인 `collect → normalize → enrich(rules+LLM) → embed → load(pgvector)` + 소스 목록; (2) 요청 흐름 `Browser → Next route handler(/api) → FastAPI → LangGraph(recommend | analyze | log) → Postgres/pgvector, NVIDIA API` + SSE 이벤트 순서. 각 그래프의 노드는 `docs/graphs.md`와 일치.
- `docs/design-decisions.md`: 질문 10개 — 왜 FastAPI(ADR 0001), 왜 LangGraph(0002), 왜 pgvector/HNSW, 왜 조건은 필터·취향은 점수, 왜 이웃 예측에 신뢰도·근거, 왜 템플릿 폴백+마감, 왜 thinking 끔(0004), 왜 임베딩 교체(0003), 왜 결정적 정렬(0006), 왜 설명 품질을 두 판정자로(0005), 데이터 라이선스 결정(오픈판/전체판), 무엇이 안 됐나(솔직한 한계). 각 답 3~6문장 + 수치 + 링크.
- README 재구성 순서: 제목·한 문단·데모 링크·GIF·스택 배지 → "결과 한눈에"(표 1개: 위반, LOO, 태그 F1, 3-way, 설명 품질, 운영 통계, 지연) → 아키텍처(축약 Mermaid 1장 + architecture.md 링크) → 빠른 시작/웹앱/배포 → 데이터 출처·라이선스 → 로드맵(3단계는 "미착수"로 솔직히) → 설계 결정 링크 → 와인 v1에서 배운 점.

- [ ] **Step 1: 테스트** — `tests/test_docs_checks.py`: 임시 md에 존재/부재 링크를 넣어 `check_links`가 부재만 보고; `check_readme_numbers`가 가짜 README+JSON에서 불일치를 잡는다.
- [ ] **Step 2: 구현·실행** — 두 스크립트 0으로 종료할 때까지 README 수정.
- [ ] **Step 3: Commit** — `git commit -m "docs: 아키텍처·설계 결정 문서, README 재구성, 링크·수치 검사"`

---

### Task 4: 잔여 정리

**Files:**
- Modify: `pipeline/query.py`(`similar`가 `active` 행만), `tests/test_query.py`(있으면; 없으면 `tests/app/test_repo.py` 옆에 DB 테스트 추가), `web/README.md`(짧게 유지 확인), 링크 검사 재실행

- [ ] **Step 1: 테스트** — 비활성 원두가 `similar` 결과에 안 나옴.
- [ ] **Step 2: 구현** — `WHERE active AND embedding IS NOT NULL …`.
- [ ] **Step 3: Commit** — `git commit -m "fix(query): 유사 검색이 비활성 원두를 제외"`
