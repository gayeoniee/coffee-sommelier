"""Markdown rendering of a refresh report (data/refresh/<date>/report.json -> report.md, the PR/issue body)."""
from __future__ import annotations

SHOW = 15


def _table(rows: list[tuple], head: tuple) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return out


def _items(title: str, rows: list, fmt, total: int | None = None) -> list[str]:
    if not rows:
        return []
    n = total if total is not None else len(rows)
    out = [f"<details><summary>{title} ({n})</summary>", ""]
    out += [f"- {fmt(r)}" for r in rows[:SHOW]]
    if n > SHOW:
        out.append(f"- … 외 {n - SHOW}개 (report.json)")
    return out + ["", "</details>", ""]


def render_markdown(d: dict) -> str:
    s = d.get("stages", {})
    mode = "DRY_RUN" if d.get("dry_run") else "실행"
    verdict = "통과" if d.get("ok") else "실패"
    out = [f"# 데이터 자동 갱신 리포트 {d['date']} ({mode})", "",
           f"- 게이트: **{verdict}** · 변경 있음: **{'예' if s.get('has_diff') else '아니오'}** · 소요 {d.get('elapsed_s')}초"]
    cfg = s.get("config") or {}
    if cfg:
        out.append(f"- 원본 `{cfg.get('source')}` → 스테이징 `{cfg.get('stage')}` / `{cfg.get('stage_open')}` → "
                   f"게시 대상 `{cfg.get('publish_full')}` / `{cfg.get('publish_open')}` · enrich 작업 `{cfg.get('enrich_task')}`")
    for e in d.get("errors") or []:
        out.append(f"- 오류: `{e}`")
    out.append("")

    col = s.get("collect") or {}
    if col and not col.get("skipped"):
        out += ["## 1. 수집", ""] + _table([(k, v) for k, v in col.items()], ("소스", "상태")) + [""]
    elif col.get("skipped"):
        out += ["## 1. 수집", "", "건너뜀(--skip-collect): 디스크의 최신 스냅샷 사용", ""]

    diff = s.get("diff") or {}
    if diff:
        m, c = diff["menus"], diff["coffees"]
        mc, cc = m["counts"], c["counts"]
        out += ["## 2. 변경 (현재 DB 대비)", ""]
        out += _table([
            ("메뉴", mc["current"], mc["collected"], mc["new"], mc["removed"],
             f"카페인 {mc['caffeine_changed']}(20%↑ {mc['caffeine_flagged']}) · 디카페인 {mc['decaf_changed']}"),
            ("원두(라이브 소스)", cc["current"], cc["collected"], cc["new"], cc["removed"], f"내용 변경 {cc['changed']}"),
        ], ("", "현재", "수집", "신규", "삭제", "변경"))
        out.append("")
        out.append(f"- 새 브랜드: {', '.join(m['new_brands']) or '없음'} · 새 로스터리/매장: "
                   f"{', '.join(c['new_roasters']) or '없음'}")
        out.append(f"- **라벨 필요 {mc['label_needed']}개** — `data/curated/menu_milk_labels.yaml`에 없는 새 메뉴명. "
                   "needs_review로 적재돼 라벨을 달기 전까지 추천에서 빠진다")
        if m.get("brands_kept_not_collected") or c.get("groups_kept_not_collected"):
            out.append("- 이번에 수집되지 않아 그대로 둔 그룹(삭제 안 함): "
                       + ", ".join(m.get("brands_kept_not_collected", []) + c.get("groups_kept_not_collected", [])))
        out.append("")
        out += _items("라벨 필요 메뉴", m["label_needed"], lambda r: r)
        out += _items("카페인 변화 20% 초과", m["caffeine_flagged"],
                      lambda r: f"{r['brand']} {r['name']}: {r['before']} → {r['after']} mg")
        out += _items("디카페인 표시 변경", m["decaf_changed"], lambda r: f"{r['brand']} {r['name']}: {r['before']} → {r['after']}")
        out += _items("신규 메뉴", m["new"], lambda r: f"{r['brand']} {r['name']}", mc["new"])
        out += _items("삭제 메뉴", m["removed"], lambda r: f"{r['brand']} {r['name']}", mc["removed"])
        out += _items("신규 원두", c["new"], lambda r: f"{r['roaster']} — {r['name']}", cc["new"])
        out += _items("삭제 원두", c["removed"], lambda r: f"{r['roaster']} — {r['name']}", cc["removed"])
        out += _items("내용이 바뀐 원두", c["changed"], lambda r: r, cc["changed"])

    ee = s.get("enrich_embed") or {}
    if ee:
        en, em = ee.get("enrich") or {}, ee.get("embed") or {}
        out += ["## 3. 보강·임베딩 (신규·변경 원두만)", "",
                f"- 대상 {ee.get('todo', 0)}개 · LLM 호출 {en.get('llm_calls', 0)}(실패 {en.get('llm_failed', 0)}, "
                f"작업 `{ee.get('enrich_task', '-')}`) · 임베딩 {em.get('embedded', 0)}개"
                f"(API 요청 {em.get('requests', 0)}, 로컬 캐시 재사용 {em.get('reused_local_cache', 0)})", ""]
    sl = s.get("stage_load") or {}
    if sl:
        out += [f"- 스테이징 적재: 원두 {sl.get('coffees')} · 메뉴 {sl.get('menu_items')} · 삭제 원두 "
                f"{sl.get('deleted_coffees')} / 메뉴 {sl.get('deleted_menu_items')} · needs_review "
                f"{sl.get('needs_review_menu_items')}", ""]

    if d.get("gates"):
        out += ["## 4. 품질 게이트", ""]
        out += _table([(g["name"], "통과" if g["ok"] else "**실패**", g["detail"]) for g in d["gates"]], ("게이트", "결과", "내용"))
        out.append("")

    pub = s.get("publish") or {}
    if pub:
        out += ["## 5. 게시 (카탈로그 테이블만 — users·tastings 불변)", ""]
        rows = []
        for label, v in pub.items():
            if not v.get("plan"):
                rows.append((label, v.get("target"), "-", v.get("skipped", "")))
                continue
            p = v["plan"]
            desc = " · ".join(f"{t} +{x['new']}/~{x['changed']}/-{x['delete']}"
                              + (f"/비활성 {x['retire']}" if x["retire"] else "")
                              for t, x in p.items() if x["new"] or x["changed"] or x["delete"] or x["retire"])
            rows.append((label, v.get("target"), "기록함" if v.get("written") else "계획만(쓰기 없음)", desc or "변경 없음"))
        out += _table(rows, ("판", "대상", "상태", "테이블별 +신규/~변경/-삭제"))
        out.append("")
    if s.get("pr"):
        out.append(f"- PR: {s['pr'].get('pr') or s['pr'].get('skipped')}")
    if s.get("issue"):
        out.append(f"- 실패 이슈: {s['issue'].get('issue') or s['issue'].get('skipped')}")
    return "\n".join(out).rstrip() + "\n"
