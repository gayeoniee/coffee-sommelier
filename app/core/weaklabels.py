"""Weak acidity/body/sweetness labels from a bean's own note words (docs/adr/0013-open-labels-weak-supervision.md).

A fixed, hand-written lexicon -- never fitted to the gauges it is later checked against:
  * each flavor tag the SCA note mapper finds (app.core.textcues.text_tags; Korean and English notes) pushes an
    attribute up or down by where it sits on the SCA wheel (level-2 node first, level-1 category as fallback):
    citrus/berry/stone-fruit/floral/sour -> acidity up, cocoa/nutty/roasted/spice -> acidity down;
    cocoa/roasted/brown sugar/dried fruit -> body up, floral/tea/citrus -> body down;
    sweet (brown sugar/honey/caramel/vanilla) and ripe/dried fruit -> sweetness up, sour/burnt/green -> down;
  * texture/intensity words in the note text itself ("syrupy", "tea-like", "juicy", "묵직한", "산뜻한") add a
    fixed push.
  label = 3 + SCALE x (mean tag push) + texture push, clipped to [1, 5]; None when neither fires.
Pure Python, same tag mapper as the feature model, so a pipeline script and the app compute identical values.
"""
import re

SCALE = 2.0

# SCA wheel node (level-2 "cat>sub" or level-1 "cat") -> push per attribute
ACIDITY = {
    "fruity>citrus fruit": 1.0, "fruity>berry": 0.8, "fruity>other fruit": 0.6, "fruity>dried fruit": 0.1,
    "fruity": 0.6, "floral>black tea": 0.2, "floral": 0.5, "sour/fermented>sour": 0.8,
    "sour/fermented>alcohol fermented": 0.4, "sour/fermented": 0.6, "green/vegetative": 0.1,
    "nutty/cocoa": -0.7, "roasted": -0.9, "spices": -0.4, "sweet>brown sugar": -0.3, "sweet": -0.1, "other": -0.3,
}
BODY = {
    "nutty/cocoa>cocoa": 0.7, "nutty/cocoa>nutty": 0.4, "roasted": 0.7, "spices": 0.3, "sweet>brown sugar": 0.4,
    "sweet>vanilla": 0.2, "sweet": 0.1, "fruity>dried fruit": 0.4, "fruity>citrus fruit": -0.5, "fruity>berry": -0.1,
    "fruity>other fruit": -0.1, "fruity": -0.1, "floral>black tea": -0.6, "floral": -0.7,
    "sour/fermented>sour": -0.4, "sour/fermented": 0.0, "green/vegetative": -0.4, "other": 0.0,
}
SWEETNESS = {
    "sweet": 0.8, "fruity>dried fruit": 0.6, "fruity>other fruit": 0.4, "fruity>berry": 0.3,
    "fruity>citrus fruit": 0.0, "fruity": 0.3, "nutty/cocoa>cocoa": 0.3, "nutty/cocoa>nutty": 0.1, "floral": 0.2,
    "sour/fermented>sour": -0.5, "sour/fermented": -0.2, "roasted>burnt": -0.8, "roasted": -0.3, "spices": -0.2,
    "green/vegetative": -0.7, "other": -0.6,
}
TAG_PUSH = {"acidity": ACIDITY, "body": BODY, "sweetness": SWEETNESS}

TEXTURE = {
    "acidity": ((re.compile(r"bright|juicy|vibrant|lively|crisp|zesty|tangy|산뜻|상큼|새콤|밝은|쥬시|주시", re.I), 0.6),
                (re.compile(r"mellow|low[\s-]acid|soft|낮은\s*산미|산미가?\s*적", re.I), -0.6)),
    "body": ((re.compile(r"syrup|heavy|creamy|full|buttery|velvet|thick|묵직|크리미|시럽|진한|무거운|쫀득|바디감", re.I), 0.8),
             (re.compile(r"tea[\s-]?like|\blight\b|clean|delicate|silky|가벼운|깔끔|산뜻|티\s*같|홍차\s*같", re.I), -0.8)),
    "sweetness": ((re.compile(r"sweet|caramel|honey|brown sugar|toffee|syrup|molasses|달콤|단맛|캐러멜|꿀|흑설탕|시럽", re.I), 0.4),
                  (re.compile(r"bitter|dry|쌉싸|쓴맛|드라이", re.I), -0.4)),
}


def tag_paths(rows) -> dict[str, str]:
    """(taxonomy key 'sca:fruity>berry>blackberry', level, name_en) rows -> tag -> 'fruity>berry' (level-2 path;
    a level-2 tag maps to itself)."""
    out: dict[str, str] = {}
    for key, level, name in rows:
        if level < 2:
            continue
        parts = key.removeprefix("sca:").split(">")
        out.setdefault(name.lower(), ">".join(parts[:2]))
    return out


def _push(table: dict[str, float], path: str) -> float | None:
    if path in table:
        return table[path]
    return table.get(path.split(">")[0])


def weak_labels(tags: list[str], paths: dict[str, str], text: str | None = None) -> dict[str, float]:
    """{attr: 1-5 weak label} for the attributes the note words say something about."""
    out: dict[str, float] = {}
    for attr, table in TAG_PUSH.items():
        pushes = [p for p in (_push(table, paths[t]) for t in tags if t in paths) if p is not None]
        texture = sum(v for pat, v in TEXTURE[attr] if text and pat.search(text))
        if not pushes and not texture:
            continue
        mean = sum(pushes) / len(pushes) if pushes else 0.0
        out[attr] = round(min(5.0, max(1.0, 3.0 + SCALE * mean + texture)), 2)
    return out
