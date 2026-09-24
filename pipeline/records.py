import json
from pathlib import Path
from typing import Iterable, TypeVar

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
