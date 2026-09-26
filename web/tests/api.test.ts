import { describe, expect, it } from "vitest";
import { tastingFor } from "@/lib/api";
import type { Card } from "@/lib/types";

const base: Card = {
  key: "k", name: "카페 아메리카노", brand: "스타벅스", score: 80, source: "brand_bean", confidence: "medium",
  acidity: 2, body: 4, sweetness: 2, tags: ["chocolate"], tags_ko: ["초콜릿"], is_decaf: false,
  order_decaf: true, decaf_surcharge_krw: 300, caffeine_mg: 150, is_milk: false, coffee_id: null,
  menu_item_id: 10, violation: null, template: "t",
};

describe("tastingFor", () => {
  it("db coffee", () => {
    expect(tastingFor({ ...base, source: "db", coffee_id: 7, menu_item_id: null }, 5, " 좋아요 "))
      .toEqual({ rating: 5, note: "좋아요", coffee_id: 7 });
  });
  it("menu item keeps order_decaf", () => {
    expect(tastingFor(base, 4, "")).toEqual({ rating: 4, note: null, menu_item_id: 10, order_decaf: true });
  });
  it("menu-less brand item sends brand + name with predicted attrs", () => {
    const t = tastingFor({ ...base, menu_item_id: null, brand: "투썸", name: "아메리카노" }, 3, "");
    expect(t.input_text).toBe("투썸 아메리카노");
    expect(t.predicted).toEqual({ acidity: 2, body: 4, sweetness: 2, tags: ["chocolate"], is_decaf: true });
  });
  it("predicted bean uses its typed text", () => {
    const t = tastingFor({ ...base, source: "predicted", menu_item_id: null, brand: null, name: "예가체프 디카페인" }, 2, "");
    expect(t.input_text).toBe("예가체프 디카페인");
  });
});
