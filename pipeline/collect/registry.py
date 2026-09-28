from pipeline.collect.datasets import CQI, ROASTERDB, SCA, KaggleCollector
from pipeline.collect.web import CoffeebeanCollector, ComposeCollector, EdiyaCollector, HollysCollector, MegaCollector, PaikCollector, PaulbassettCollector, ShopifyCollector, ShopifyGaugedCollector, StarbucksCollector

ALL_COLLECTORS = [
    CQI, ROASTERDB, SCA, KaggleCollector(),
    StarbucksCollector(), MegaCollector(), PaikCollector(), ShopifyCollector(),
    CoffeebeanCollector(), ComposeCollector(), HollysCollector(), PaulbassettCollector(),
    ShopifyGaugedCollector(), EdiyaCollector(),
]
