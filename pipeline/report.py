import json

FIELDS = ["origin_country", "process", "roast_level", "acidity", "body", "sweetness", "flavor_tags", "embedding"]


def build_report(conn, stage_stats: dict[str, dict]) -> str:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    total = q("SELECT count(*) FROM coffees")
    lines = ["# 데이터 품질 리포트", "", "## 테이블", "", "| 항목 | 행 수 |", "|---|---|"]
    for t in ["coffees", "reviews", "brands", "menu_items", "flavor_taxonomy"]:
        lines.append(f"| {t} | {q(f'SELECT count(*) FROM {t}')} |")
    lines.append(f"| decaf coffees | {q('SELECT count(*) FROM coffees WHERE is_decaf')} |")
    lines.append(f"| decaf menu items | {q('SELECT count(*) FROM menu_items WHERE is_decaf')} |")

    lines += ["", "## 소스별 원두", "", "| source | 행 수 |", "|---|---|"]
    for source, n in conn.execute("SELECT source, count(*) FROM coffees GROUP BY source ORDER BY 2 DESC").fetchall():
        lines.append(f"| {source} | {n} |")

    lines += ["", "## 필드 결측률 (coffees)", "", "| field | missing |", "|---|---|"]
    for f in FIELDS:
        cond = "cardinality(flavor_tags) = 0" if f == "flavor_tags" else f"{f} IS NULL"
        missing = q(f"SELECT count(*) FROM coffees WHERE {cond}")
        rate = 100.0 * missing / total if total else 0.0
        lines.append(f"| {f} | {rate:.1f}% |")

    failed = q("SELECT count(*) FROM enrich_log WHERE status = 'failed'")
    lines += ["", "## enrich", "", f"enrich failed: {failed}",
              "", "## 단계 통계", "", "```json", json.dumps(stage_stats, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)
