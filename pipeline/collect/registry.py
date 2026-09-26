from pipeline.collect.datasets import CQI, ROASTERDB, SCA, KaggleCollector
from pipeline.collect.web import HollysCollector, MegaCollector, PaikCollector, ShopifyCollector, StarbucksCollector

ALL_COLLECTORS = [
    CQI, ROASTERDB, SCA, KaggleCollector(),
    StarbucksCollector(), MegaCollector(), PaikCollector(), ShopifyCollector(), HollysCollector(),
]
