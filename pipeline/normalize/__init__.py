from dataclasses import dataclass, field

from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode


@dataclass
class Normalized:
    coffees: list[CoffeeRecord] = field(default_factory=list)
    reviews: list[ReviewRecord] = field(default_factory=list)
    brands: list[BrandRecord] = field(default_factory=list)
    menu_items: list[MenuItemRecord] = field(default_factory=list)
    taxonomy: list[TaxonomyNode] = field(default_factory=list)

    def extend(self, other: "Normalized") -> None:
        self.coffees += other.coffees
        self.reviews += other.reviews
        self.brands += other.brands
        self.menu_items += other.menu_items
        self.taxonomy += other.taxonomy

    def size(self) -> int:
        return len(self.coffees) + len(self.reviews) + len(self.brands) + len(self.menu_items) + len(self.taxonomy)
