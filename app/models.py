from dataclasses import asdict, dataclass, field

ATTRS = ("acidity", "body", "sweetness")
CAFFEINE_RULES = ("decaf_only", "low", "any")
CONFIDENCE_LEVELS = ("low", "medium", "high")


def cap_confidence(confidence: str, values) -> str:
    """A card that cannot show one of acidity/body/sweetness is not "high" confidence: one missing core attribute
    caps it at "medium", two or more at "low" (never raises a lower level)."""
    missing = sum(v is None for v in values)
    cap = "high" if missing == 0 else "medium" if missing == 1 else "low"
    return min(confidence, cap, key=CONFIDENCE_LEVELS.index)


@dataclass(frozen=True)
class Item:
    """Anything that can be recommended or logged: a DB coffee, a menu drink, or a predicted bean."""
    key: str                      # "coffee:12" | "menu:34" | "brand:twosome:아메리카노" | "input"
    name: str
    source: str                   # "db" | "brand_bean" | "predicted"
    acidity: float | None = None
    body: float | None = None
    sweetness: float | None = None
    tags: tuple[str, ...] = ()
    is_decaf: bool = False
    decaf_option: bool = False
    order_decaf: bool = False     # recommend ordering the decaf version of this drink
    caffeine_mg: float | None = None    # shown caffeine; for order_decaf the decaf estimate, never the regular mg
    caffeine_mg_note: str | None = None  # set when caffeine_mg is not the drink's own measured value (decaf order)
    is_milk: bool = False
    confidence: str = "high"      # high | medium | low
    brand: str | None = None
    decaf_surcharge_krw: int | None = None
    coffee_id: int | None = None
    menu_item_id: int | None = None
    origin_country: str | None = None
    process: str | None = None
    bean_note: str | None = None    # franchise drink: the brand's official bean line (docs/adr/0012), card evidence

    def attr(self, name: str) -> float | None:
        return getattr(self, name)


@dataclass
class Profile:
    caffeine_rule: str = "any"
    milk_ok: bool = True
    acidity: float = 3.0
    body: float = 3.0
    sweetness: float = 3.0
    flavor_weights: dict[str, float] = field(default_factory=dict)   # SCA category -> [-1, 1]
    n_updates: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class Neighbor:
    coffee_id: int
    name: str
    similarity: float
    acidity: float | None
    body: float | None
    sweetness: float | None
    tags: tuple[str, ...] = ()


@dataclass
class Prediction:
    acidity: float | None
    body: float | None
    sweetness: float | None
    confidence: str
    tags: list[str]
    evidence: list[str]
    n_neighbors: int


@dataclass(frozen=True)
class ParsedBean:
    text: str
    origin_country: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool = False
    decaf_process: str | None = None
