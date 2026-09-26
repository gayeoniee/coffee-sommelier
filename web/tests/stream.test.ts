import { describe, expect, it } from "vitest";
import { initialStream, reduceStream, type StreamState } from "@/lib/stream";
import type { Card } from "@/lib/types";

const card = (key: string): Card => ({
  key, name: key, brand: "스타벅스", score: 80, source: "brand_bean", confidence: "medium",
  acidity: 2, body: 4, sweetness: 2, tags: [], tags_ko: [], is_decaf: false, order_decaf: false,
  decaf_surcharge_krw: null, caffeine_mg: 150, is_milk: false, coffee_id: null, menu_item_id: 1,
  violation: null, template: `템플릿 ${key}`,
});

describe("reduceStream", () => {
  it("cards → deltas → done / fallback", () => {
    let s: StreamState = { ...initialStream, status: "loading" };
    s = reduceStream(s, { event: "cards", data: { cards: [card("a"), card("b")] } });
    expect(s.cards.map((c) => c.key)).toEqual(["a", "b"]);
    expect(s.explanations.a).toEqual({ text: "", status: "streaming" });
    s = reduceStream(s, { event: "explain_delta", data: { key: "a", delta: "산미가 " } });
    s = reduceStream(s, { event: "explain_delta", data: { key: "a", delta: "좋아요" } });
    expect(s.explanations.a.text).toBe("산미가 좋아요");
    s = reduceStream(s, { event: "explain_done", data: { key: "a", text: "산미가 좋아요." } });
    s = reduceStream(s, { event: "explain_fallback", data: { key: "b", text: "템플릿 b" } });
    expect(s.explanations.a).toEqual({ text: "산미가 좋아요.", status: "done" });
    expect(s.explanations.b).toEqual({ text: "템플릿 b", status: "fallback" });
    s = reduceStream(s, { event: "done", data: {} });
    expect(s.status).toBe("done");
  });

  it("empty and error", () => {
    let s = reduceStream({ ...initialStream, status: "loading" }, { event: "empty", data: { reason: "메뉴 없음" } });
    expect(s.empty).toBe("메뉴 없음");
    s = reduceStream(s, { event: "error", data: { message: "오류" } });
    s = reduceStream(s, { event: "done", data: {} });
    expect(s.status).toBe("error");
    expect(s.error).toBe("오류");
  });

  it("done/error settle explanations that never finished to the card template", () => {
    let s: StreamState = { ...initialStream, status: "loading" };
    s = reduceStream(s, { event: "cards", data: { cards: [card("a"), card("b")] } });
    s = reduceStream(s, { event: "explain_delta", data: { key: "a", delta: "산미" } });
    s = reduceStream(s, { event: "explain_done", data: { key: "b", text: "끝" } });
    s = reduceStream(s, { event: "error", data: { message: "오류" } });
    expect(s.explanations.a).toEqual({ text: "템플릿 a", status: "fallback" });
    expect(s.explanations.b).toEqual({ text: "끝", status: "done" });
  });

  it("ignores unknown events", () => {
    expect(reduceStream(initialStream, { event: "ping", data: {} })).toBe(initialStream);
  });
});
