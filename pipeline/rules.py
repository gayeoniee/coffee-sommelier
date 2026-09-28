import re
import sys

# pandas/numpy stay out of the module import: the API image (see Dockerfile) ships without them,
# and the app only needs the text rules below. Pipeline code that passes pandas values has pandas loaded.


def _isna(v) -> bool:
    """pd.isna for scalars, without importing pandas."""
    if v is None:
        return True
    pd = sys.modules.get("pandas")
    if pd is not None:
        return bool(pd.isna(v))
    return isinstance(v, float) and v != v


def clean(v) -> str | None:
    if v is None:
        return None
    try:
        if _isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def num(v) -> float | None:
    s = clean(v)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def opt_int(v) -> int | None:
    return None if _isna(v) else int(v)


def join_text(*parts) -> str | None:
    kept = [p for p in (clean(x) for x in parts) if p]
    return "\n".join(kept) or None


# --- decaf -------------------------------------------------------------
DECAF_WORD = re.compile(r"(?<![a-z])(?:decaf|decaffeinat)|디카페인", re.I)
# (name, pattern, counts-without-the-word-decaf)
DECAF_PROCESSES = [
    ("swiss-water", re.compile(r"swiss\s*water", re.I), True),
    ("mountain-water", re.compile(r"mountain\s*water", re.I), True),
    ("sugarcane-ea", re.compile(r"sugar\s*cane|ethyl\s*acetate|\bE\.?A\.?\s+process|슈가\s*케인|사탕수수", re.I), False),
    ("co2", re.compile(r"\bco2\b|carbon dioxide|supercritical", re.I), False),
    ("methylene-chloride", re.compile(r"methylene chloride|european process", re.I), False),
]


def detect_decaf(*texts) -> tuple[bool, str | None]:
    t = " ".join(x for x in (clean(v) for v in texts) if x)
    has_word = DECAF_WORD.search(t) is not None
    for name, pat, standalone in DECAF_PROCESSES:
        if pat.search(t) and (standalone or has_word):
            return True, name
    return (True, "unknown") if has_word else (False, None)


# --- country -------------------------------------------------------------
_COUNTRIES = [
    "Ethiopia", "Kenya", "Colombia", "Brazil", "Guatemala", "Costa Rica", "Panama", "Honduras",
    "El Salvador", "Nicaragua", "Mexico", "Peru", "Bolivia", "Ecuador", "Rwanda", "Burundi",
    "Uganda", "Tanzania", "Democratic Republic of the Congo", "Malawi", "Zambia", "Zimbabwe",
    "Yemen", "India", "Indonesia", "Papua New Guinea", "Vietnam", "Thailand", "Laos", "Myanmar",
    "China", "Taiwan", "Philippines", "Jamaica", "Haiti", "Dominican Republic", "Puerto Rico",
    "Cuba", "Venezuela", "United States", "Mauritius", "Madagascar", "Cameroon", "Timor-Leste",
    "Nepal", "Australia",
]
_ALIASES = {
    "hawaii": "United States", "kona": "United States", "usa": "United States",
    "sumatra": "Indonesia", "java": "Indonesia", "sulawesi": "Indonesia", "bali": "Indonesia",
    "congo": "Democratic Republic of the Congo", "east timor": "Timor-Leste",
    "cote d'ivoire": "Côte d'Ivoire", "cote d?ivoire": "Côte d'Ivoire", "côte d'ivoire": "Côte d'Ivoire",
    "yirgacheffe": "Ethiopia", "sidama": "Ethiopia", "guji": "Ethiopia", "harrar": "Ethiopia",
}
_KO = {
    "에티오피아": "Ethiopia", "케냐": "Kenya", "콜롬비아": "Colombia", "브라질": "Brazil",
    "과테말라": "Guatemala", "코스타리카": "Costa Rica", "파나마": "Panama", "온두라스": "Honduras",
    "엘살바도르": "El Salvador", "니카라과": "Nicaragua", "멕시코": "Mexico", "페루": "Peru",
    "볼리비아": "Bolivia", "에콰도르": "Ecuador", "르완다": "Rwanda", "부룬디": "Burundi",
    "우간다": "Uganda", "탄자니아": "Tanzania", "예멘": "Yemen", "인도네시아": "Indonesia",
    "파푸아뉴기니": "Papua New Guinea", "베트남": "Vietnam", "중국": "China", "하와이": "United States",
    "예가체프": "Ethiopia", "시다마": "Ethiopia", "구지": "Ethiopia",
    "인도": "India",   # after 인도네시아: on a tie at the same position the earlier entry wins
}
_COUNTRY_PATTERNS = [(re.compile(rf"(?<![a-z]){re.escape(c.lower())}(?![a-z])"), c) for c in _COUNTRIES]
_COUNTRY_PATTERNS += [(re.compile(rf"(?<![a-z]){re.escape(a)}(?![a-z])"), c) for a, c in _ALIASES.items()]
_COUNTRY_PATTERNS += [(re.compile(re.escape(k)), c) for k, c in _KO.items()]


def normalize_country(text) -> str | None:
    t = clean(text)
    if not t:
        return None
    t = t.lower()
    best: tuple[int, str] | None = None
    for pat, canon in _COUNTRY_PATTERNS:
        m = pat.search(t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), canon)
    return best[1] if best else None


# --- process -------------------------------------------------------------
_LABEL_PROCESS = [
    ("anaerobic", re.compile(r"anaerob|carbonic|maceration|무산소|애너로빅", re.I)),
    ("wet-hulled", re.compile(r"wet[\s-]?hull|giling", re.I)),
    ("honey", re.compile(r"honey|pulped natural|miel|허니", re.I)),
    ("semi-washed", re.compile(r"semi[\s-]?(washed|lavado|pulped)", re.I)),
    ("natural", re.compile(r"natural|\bdry\b|내추럴", re.I)),
    ("washed", re.compile(r"washed|\bwet\b|lavado|워시드", re.I)),
]
_TEXT_PROCESS = [
    ("anaerobic", re.compile(r"anaerobic|carbonic maceration|무산소|애너로빅", re.I)),
    ("wet-hulled", re.compile(r"wet[\s-]hull", re.I)),
    ("honey", re.compile(r"honey[\s-]process|pulped[\s-]natural|허니\s*프로세스", re.I)),
    ("natural", re.compile(r"dry[\s-]process|natural[\s-]process|\(natural|natural method|내추럴", re.I)),
    ("washed", re.compile(r"wet[\s-]process|washed[\s-]process|fully washed|\(washed\)|\bwashed\b|워시드", re.I)),
]


def normalize_process(label) -> str | None:
    t = clean(label)
    if not t:
        return None
    for name, pat in _LABEL_PROCESS:
        if pat.search(t):
            return name
    return None


def process_from_text(text) -> str | None:
    t = clean(text)
    if not t:
        return None
    for name, pat in _TEXT_PROCESS:
        if pat.search(t):
            return name
    return None


# --- altitude ------------------------------------------------------------
def parse_altitude_m(text) -> int | None:
    """Midpoint of a labelled altitude ("1,950-2,050m", "1,066m", "1600~2000 masl", 1850.0) in metres;
    implausible values (outside 200-3000 m -- CQI has typos like 190164) are dropped."""
    t = clean(text)
    if not t:
        return None
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d{1,2},\d{3}(?:\.\d+)?|\d+(?:\.\d+)?", t)[:2]]
    nums = [n for n in nums if n > 0]
    if not nums:
        return None
    alt = round(sum(nums) / len(nums))
    return alt if 200 <= alt <= 3000 else None


def round_half_up(v: float | None) -> int | None:
    """1-5 label from a half-step gauge (3.5 -> 4): Python's round() would send 2.5 and 4.5 to the even
    neighbour, which is not what a roaster's "4.5 of 5" means."""
    return None if v is None else int(v + 0.5)


# --- roast ---------------------------------------------------------------
_ROAST = [
    ("medium-light", re.compile(r"medium[\s-]*light|light[\s-]*medium|미디엄\s*라이트", re.I)),
    ("medium-dark", re.compile(r"medium[\s-]*dark|미디엄\s*다크", re.I)),
    ("light", re.compile(r"\blight\b|라이트|약배전|blonde|블론드", re.I)),
    ("dark", re.compile(r"dark|다크|강배전|french|italian", re.I)),
    ("medium", re.compile(r"medium|미디엄|중배전", re.I)),
]


def normalize_roast(text) -> str | None:
    t = clean(text)
    if not t:
        return None
    for name, pat in _ROAST:
        if pat.search(t):
            return name
    return None


# --- scores --------------------------------------------------------------
def to_quintile(values):
    """Map numeric scores to 1-5 by within-source percentile rank. Missing stays <NA>. Returns a pd.Series."""
    import numpy as np
    import pandas as pd

    s = pd.to_numeric(pd.Series(values), errors="coerce")
    ranks = s.rank(pct=True, method="average")
    q = np.ceil(ranks * 5 - 1e-9).clip(1, 5)
    return q.astype("Int64")
