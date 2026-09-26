# 포트폴리오 마감: PWA 설치, 데모 GIF, 아키텍처 다이어그램, 설계 결정 문서, 잔여 정리 (설계)

2026-09-27. 완성도 로드맵 4/4(마지막). 앞의 세 작업이 만든 상태를 "읽는 사람"(채용 담당자·면접관·심사위원) 기준으로 마감한다.

## 1. 읽는 사람이 3분 안에 알아야 할 것

1. 무엇을 만들었고 왜 — 첫 화면(README 상단)에서 데모 링크·GIF·한 문단.
2. 어떻게 도는가 — 다이어그램 한 장(수집→정규화→구조화→임베딩→pgvector; 앱: FastAPI + LangGraph 그래프 3개, SSE, Next 프록시, 배포 3곳).
3. 왜 그렇게 결정했나 — ADR 목록과 "면접에서 물을 만한 질문 10개"에 대한 답(설계 결정 문서).
4. 얼마나 믿을 수 있나 — 평가 표(조건 위반 0/102, LOO·태그 F1, 3-way 비교, 설명 품질 판정, 운영 통계) 한 곳에 모아 링크.

## 2. 목표 / 비목표

목표
- **PWA 설치 경험**: 아이콘(192/512, maskable), `manifest.ts` 보강(icons, categories, screenshots), 최소 서비스 워커(앱 셸 캐시 + 오프라인 안내 페이지; API는 캐시하지 않음), iOS `apple-touch-icon`. Lighthouse PWA 항목 통과(로컬 `npx lighthouse` 결과를 docs에 기록).
- **데모 GIF**: Playwright로 폰 뷰포트 시나리오(온보딩 → 할리스 추천 → 카드·스트리밍 설명 → 기록 → 내 취향)를 프레임 캡처해 GIF(≤ 6MB, 20~30초) 생성 스크립트. README 상단에 삽입.
- **아키텍처 다이어그램**: `docs/architecture.md`에 Mermaid 2장(데이터 파이프라인, 요청 흐름) + README에 축약 1장.
- **설계 결정 문서**: `docs/design-decisions.md` — 질문-답 형식 10개(왜 FastAPI/LangGraph/pgvector, 왜 필터를 점수에 안 넣나, 왜 이웃 예측에 신뢰도, 왜 템플릿 폴백, 왜 thinking 끔, 왜 결정적 정렬, 데이터 라이선스 선택, 무엇이 안 됐나). 각 답은 수치·ADR 링크 포함.
- **README 재구성**: 상단(데모·GIF·한 문단·스택 배지) → 결과 표 한 곳 → 아키텍처 → 실행/배포 → 데이터 출처 → 로드맵. 기존 내용은 자르지 않고 순서·제목만 정리, 3단계 로드맵의 "예정"을 솔직한 상태로.
- **잔여 정리**: `pipeline/query.similar`가 `active`를 무시하는 것, 컴포즈 「올데이 오트」·할리스 콜드브루 등 fix-round 반영 후 남은 minor(ledger 목록) 중 30분 안에 끝나는 것만. `web/README.md`·`docs/deploy.md` 링크 점검(깨진 링크 스크립트).

비목표
- 새 기능. 로그인. 디자인 리뉴얼(색·레이아웃 유지).
- 3단계(사진 스캔·Vision) 착수.

## 3. 세부

### 3.1 PWA
- 아이콘은 코드로 생성(Pillow 또는 SVG→PNG): 크림 배경에 커피잔 실루엣 + "☕" 텍스트 금지(폰트 의존) → 단순 도형. `web/public/icons/icon-192.png`, `icon-512.png`, `maskable-512.png`, `apple-touch-icon.png`.
- 서비스 워커: `web/public/sw.js`(수동, 의존성 추가 없음) — install에서 `/`, `/onboarding`, `/me`, `/offline` 프리캐시(버전 상수), fetch에서 navigation 요청은 network-first → 실패 시 캐시 → `/offline`; `/api/*`와 `_next/static` 이외는 통과. `app/layout.tsx`에서 등록(프로덕션에서만). `app/offline/page.tsx` 한 문장.
- 테스트: Vitest로 `sw.js`의 fetch 라우팅 함수(순수 함수로 분리 `sw-routes.js`)만 검증. E2E는 기존 스모크 유지.

### 3.2 데모 GIF
- `web/scripts/demo-gif.mjs`: Playwright(iPhone 13) + `page.screenshot` 프레임 500ms 간격 → `gifenc`(dev 의존성, 순수 JS)로 인코딩. 실제 백엔드 필요(로컬 uvicorn). 산출 `docs/demo.gif`. README 상단 `![demo](docs/demo.gif)`.

### 3.3 문서
- `docs/architecture.md`, `docs/design-decisions.md`, README 재구성. 링크 검사 스크립트 `scripts/check_links.py`(로컬 상대 링크 존재 여부만).

## 4. 검증
- `cd web && npm test && npm run lint && npx tsc --noEmit && npm run build`; Lighthouse PWA 통과 캡처(`docs/screenshots/lighthouse.png`); GIF 크기 ≤ 6MB; `scripts/check_links.py` 0 broken; `uv run pytest -q`.
