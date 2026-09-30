import json
from pathlib import Path
from typing import Iterable, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class CoffeeRecord(BaseModel):
    key: str
    name: str
    roaster: str | None = None
    origin_country: str | None = None
    origin_region: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool = False
    decaf_process: str | None = None
    acidity: int | None = Field(default=None, ge=1, le=5)
    body: int | None = Field(default=None, ge=1, le=5)
    sweetness: int | None = Field(default=None, ge=1, le=5)
    flavor_tags: list[str] = Field(default_factory=list)
    flavor_summary: str | None = None
    altitude_m: int | None = None          # labelled growing altitude (CQI, some roastery pages), metres
    variety: str | None = None             # labelled cultivar, as written by the source
    # Where each attribute label came from, per attribute: gauge (a roaster's published intensity gauge),
    # korean_cue (a note-word rule), llm_review (LLM judged from the bean's own text), cqi_quality (a CQI
    # cupping quality-score quintile), review_score (a coffeereview sub-score quintile). An attribute
    # with no value has no entry. docs/adr/0011-roaster-gauges-feature-model.md
    attr_label_source: dict[str, str] = Field(default_factory=dict)
    source: str
    source_url: str | None = None
    collected_at: str


class ReviewRecord(BaseModel):
    key: str
    coffee_key: str
    text: str
    rating: float | None = None
    sub_scores: dict[str, float] = Field(default_factory=dict)
    source: str
    source_url: str | None = None
    collected_at: str


class BeanProfile(BaseModel):
    """Taste of a brand's house/decaf bean. `label_source` says where each value came from
    (docs/adr/0012-official-brand-beans.md): the brand's published gauge, the learned models + text cues run on
    the brand's official description (scripts/derive_brand_beans.py), or a hand estimate. `official_note` is the
    one-line official description shown as card evidence (None when the brand publishes none).

    The open/competition variant (DATA_VARIANT=open, docs/adr/0011) has its own licence-clean profile per bean
    (BrandRecord.bean_open / decaf_bean_open, same script with `--variant open`): official_gauge, official_cue
    (a text cue or flavor word in the brand's own copy, app/core/textcues.py), open_feature_model (the
    roaster-gauge feature model, app/core/featuremodel.py) or estimate -- never official_notes_model, whose
    learned models were trained on licence-restricted coffeereview labels (docs/adr/0008, 0009)."""
    acidity: float = Field(ge=1, le=5)
    body: float = Field(ge=1, le=5)
    sweetness: float = Field(ge=1, le=5)
    flavor_tags: list[str] = Field(default_factory=list)
    label_source: dict[str, Literal["official_gauge", "official_notes_model", "official_cue", "open_feature_model",
                                    "estimate"]] = Field(default_factory=dict)
    official_note: str | None = None


class BrandRecord(BaseModel):
    key: str
    name: str
    decaf_available: bool
    decaf_surcharge_krw: int | None = None
    default_bean_coffee_key: str | None = None
    decaf_bean_coffee_key: str | None = None
    notes: str | None = None
    source_url: str | None = None
    verified_at: str
    bean: BeanProfile | None = None
    decaf_bean: BeanProfile | None = None
    bean_open: BeanProfile | None = None        # DATA_VARIANT=open counterparts (licence-clean sources only)
    decaf_bean_open: BeanProfile | None = None
    decaf_option_categories: list[str] = Field(default_factory=list)


class MenuItemRecord(BaseModel):
    key: str
    brand_key: str
    name: str
    name_en: str | None = None
    category: str | None = None
    is_decaf: bool = False
    decaf_option: bool = False
    caffeine_mg: float | None = None
    coffee_key: str | None = None
    source: str | None = None      # e.g. "mfds_food" (docs/adr/0023); None for the brand's own site collector
    source_url: str | None = None
    collected_at: str


class TaxonomyNode(BaseModel):
    key: str
    parent_key: str | None = None
    level: int
    name_en: str
    name_ko: str | None = None


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")
            n += 1
    return n


def read_jsonl(path: Path, model: type[T]) -> list[T]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [model.model_validate(json.loads(line)) for line in f if line.strip()]
