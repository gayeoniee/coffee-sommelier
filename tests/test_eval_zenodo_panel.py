"""scripts/eval_zenodo_panel.py: the external panel's truth mapping and bean-card text (no data file needed)."""
from scripts.eval_zenodo_panel import attr_metrics, body_value, english_name, roast_word, samples_from_rows, spearman

HEADER = [["Sample Info"], ["Sample №", "Panelist", "Sample name", "Grind / Roast"]]


def _row(sid, name, roast, aroma, acid, sweet, body):
    r = [None] * 21
    r[0], r[1], r[2], r[3], r[4], r[6], r[8] = sid, "p", name, roast, aroma, "caramel", "short"
    r[11], r[14], r[19] = acid, sweet, body
    return r


def test_body_words_map_weight_not_texture_and_phrases_first():
    assert body_value("dense, rough") == 4
    assert body_value("below average, smooth") == 2          # not also "average"
    assert body_value("light, enveloping") == 3              # mean of 2 and 4
    assert body_value("smooth, silky") is None               # texture only


def test_russian_origin_and_process_words_are_translated():
    assert english_name("Tasty coffee- Эфиопия Иргачефф Нат") == "Tasty coffee Ethiopia Иргачефф natural"
    assert english_name("Tasty coffee- Смесь Натти") == "Tasty coffee blend Натти"   # "Натти" is not "Нат"
    assert english_name("Welder Catherine- Коста- Рика Хуанра Монтеро").startswith("Welder Catherine Costa Rica")
    assert roast_word("Filter roast, fine grind") == "light" and roast_word("Dark roast") == "dark"


def test_samples_average_the_panel_and_keep_answers_out_of_the_text():
    rows = HEADER + [_row(1, "Rockets coffee- Dekaf", "Espresso roast", "nuts, cocoa", "low", "middle", "dense"),
                     _row(1, None, None, "cocoa, berries", "below middle", "high", "light")]
    [s] = samples_from_rows(rows)
    assert s["truth"] == {"acidity": 1.5, "sweetness": 4.0, "body": 3.0} and s["panelists"] == 2
    assert s["is_decaf"] and "decaf" in s["text"] and "medium roast" in s["text"]
    assert "notes: nuts, cocoa, caramel, berries" in s["text"]
    assert "dense" not in s["text"] and "middle" not in s["text"]


def test_metrics():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    m = attr_metrics([(1, 2), (3, 3), (5, 3)])
    assert (m["n"], m["mae"], m["within1"]) == (3, 1.0, 0.6667)
