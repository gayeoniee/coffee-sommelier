def to_vector_literal(vec) -> str:
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def similar(conn, embedder, text: str, k: int = 5, decaf: bool | None = None) -> list[dict]:
    v = to_vector_literal(embedder.embed_query(text))
    where = "active AND embedding IS NOT NULL" + (" AND is_decaf = %(decaf)s" if decaf is not None else "")
    sql = (
        "SELECT name, roaster, origin_country, process, is_decaf, decaf_process, acidity, body, flavor_tags,"
        f" 1 - (embedding <=> %(v)s::vector) AS score FROM coffees WHERE {where}"
        " ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s"
    )
    # pgvector filters after the HNSW scan; iterative scan keeps searching until k filtered rows are found
    # (relaxed_order may return rows slightly out of order, so re-sort).
    with conn.transaction():
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        conn.execute("SET LOCAL hnsw.ef_search = 200")
        cur = conn.execute(sql, {"v": v, "k": k, "decaf": decaf})
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return sorted(rows, key=lambda r: r["score"], reverse=True)
