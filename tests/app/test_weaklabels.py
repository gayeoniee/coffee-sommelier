from app.core.weaklabels import tag_paths, weak_labels

ROWS = [
    ("sca:fruity>citrus fruit", 2, "citrus fruit"), ("sca:fruity>citrus fruit>lemon", 3, "lemon"),
    ("sca:fruity>berry>raspberry", 3, "raspberry"), ("sca:floral>floral>jasmine", 3, "jasmine"),
    ("sca:nutty/cocoa>cocoa>dark chocolate", 3, "dark chocolate"), ("sca:roasted>burnt>smoky", 3, "smoky"),
    ("sca:sweet>brown sugar>caramelized", 3, "caramelized"), ("sca:fruity", 1, "fruity"),
]
PATHS = tag_paths(ROWS)


def test_tag_paths_level2():
    assert PATHS == {"citrus fruit": "fruity>citrus fruit", "lemon": "fruity>citrus fruit", "raspberry": "fruity>berry",
                     "jasmine": "floral>floral", "dark chocolate": "nutty/cocoa>cocoa", "smoky": "roasted>burnt",
                     "caramelized": "sweet>brown sugar"}


def test_directions():
    bright = weak_labels(["lemon", "raspberry", "jasmine"], PATHS)
    dark = weak_labels(["dark chocolate", "smoky"], PATHS)
    assert bright["acidity"] > 4 > 3 > dark["acidity"]
    assert bright["body"] < 3 < dark["body"]
    assert weak_labels(["caramelized"], PATHS)["sweetness"] > 4


def test_texture_words_and_clipping():
    assert weak_labels([], PATHS, "syrupy, heavy body")["body"] == 3.8
    assert weak_labels([], PATHS, "깔끔하고 가벼운")["body"] == 2.2
    assert weak_labels(["lemon"] * 3, PATHS, "bright juicy")["acidity"] == 5.0
    assert weak_labels([], PATHS, "") == {}
    assert weak_labels(["unknown tag"], PATHS) == {}
