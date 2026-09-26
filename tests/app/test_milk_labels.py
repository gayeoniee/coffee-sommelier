"""Golden test: the app's milk detector against the hand-labelled menu names."""
import yaml

from app.core.scoring import is_milk_drink
from pipeline import settings

# Names whose label the keyword detector is allowed to miss (reason each). Keep at most 5.
KNOWN_EXCEPTIONS: dict[str, str] = {}

LABELS: dict[str, bool] = yaml.safe_load(
    (settings.CURATED_DIR / "menu_milk_labels.yaml").read_text(encoding="utf-8"))


def test_labels_cover_synthetic_menu_and_are_booleans():
    assert LABELS["아메리카노"] is False and LABELS["카페라떼"] is True
    assert all(isinstance(v, bool) for v in LABELS.values()) and len(LABELS) > 250
    assert len(KNOWN_EXCEPTIONS) <= 5


def test_is_milk_drink_matches_every_hand_label():
    wrong = {n: lab for n, lab in LABELS.items() if n not in KNOWN_EXCEPTIONS and is_milk_drink(n) != lab}
    assert wrong == {}


def test_is_milk_drink_normalises_case_and_spaces():
    assert is_milk_drink("Flat White") and is_milk_drink("카 페 라 떼") and is_milk_drink("ICED CAFE LATTE")
    assert not is_milk_drink("Cold Brew") and not is_milk_drink("아이스 카페 아메리카노")
