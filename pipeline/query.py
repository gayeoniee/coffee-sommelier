def to_vector_literal(vec) -> str:
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def similar(conn, embedder, text: str, k: int = 5, decaf: bool | None = None) -> list[dict]:
    v = to_vector_literal(embedder.embed([text])[0])
    where = "embedding IS NOT NULL" + (" AND is_decaf = %(decaf)s" if decaf is not None else "")
    sql = (
        "SELECT name, roaster, origin_country, process, is_decaf, decaf_process, acidity, body, flavor_tags,"
        f" 1 - (embedding <=> %(v)s::vector) AS score FROM coffees WHERE {where}"
        " ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s"
    )
    cur = conn.execute(sql, {"v": v, "k": k, "decaf": decaf})
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
