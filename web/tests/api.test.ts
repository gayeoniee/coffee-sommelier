import { afterEach, describe, expect, it, vi } from "vitest";
import { api, openStream, tastingFor } from "@/lib/api";
import type { Card } from "@/lib/types";

afterEach(() => vi.unstubAllGlobals());

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

describe("lost session recovery", () => {
  it("req: a 401 makes a fresh /session and retries the request once", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn((url: string) => {
      calls.push(url);
      if (url === "/api/me" && calls.filter((u) => u === "/api/me").length === 1) {
        return Promise.resolve(new Response(JSON.stringify({ detail: "세션이 없어요" }), { status: 401 }));
      }
      if (url === "/api/session") {
        return Promise.resolve(new Response(JSON.stringify({ user_id: "u2", new: true, has_profile: false }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify({ user_id: "u2", nickname: null, profile: null, history: [], tastings: [] }), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const me = await api.me();
    expect(me.user_id).toBe("u2");
    expect(calls).toEqual(["/api/me", "/api/session", "/api/me"]);
  });

  it("req: a second consecutive 401 (session recovery didn't help) still throws", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/session") return Promise.resolve(new Response("{}", { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify({ detail: "세션이 없어요" }), { status: 401 }));
    }));
    await expect(api.me()).rejects.toMatchObject({ status: 401 });
  });

  it("openStream: a 401 recovers the session and retries the stream request once", async () => {
    const calls: string[] = [];
    const body = new ReadableStream<Uint8Array>({ start(c) { c.close(); } });
    const fetchMock = vi.fn((url: string) => {
      calls.push(url);
      if (url === "/api/recommend" && calls.filter((u) => u === "/api/recommend").length === 1) {
        return Promise.resolve(new Response(JSON.stringify({ detail: "세션이 없어요" }), { status: 401 }));
      }
      if (url === "/api/session") return Promise.resolve(new Response("{}", { status: 200 }));
      return Promise.resolve(new Response(body, { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const stream = await openStream("/recommend", { brand_key: "sb" });
    expect(stream).toBeInstanceOf(ReadableStream);
    expect(calls).toEqual(["/api/recommend", "/api/session", "/api/recommend"]);
  });
});
