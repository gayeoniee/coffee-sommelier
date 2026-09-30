import { describe, expect, it } from "vitest";
import { defaultDraft, profileInput, sampleAnswers } from "@/lib/onboarding";

describe("onboarding", () => {
  it("builds the PUT /me/profile body", () => {
    const d = { ...defaultDraft, caffeine_rule: "decaf_only" as const, acidity: 4.5, likes: ["fruity", "floral"] };
    expect(profileInput(d, " 가연 ")).toEqual({
      caffeine_rule: "decaf_only", milk_ok: true, acidity: 4.5, body: 3, sweetness: 3,
      flavor_likes: ["fruity", "floral"], daily_caffeine_limit_mg: null, nickname: "가연",
    });
    expect(profileInput(d).nickname).toBeUndefined();
  });
  it("carries the daily caffeine limit through", () => {
    const d = { ...defaultDraft, dailyCaffeineLimitMg: 300 };
    expect(profileInput(d).daily_caffeine_limit_mg).toBe(300);
  });
  it("only answered samples are sent", () => {
    expect(sampleAnswers({ 1: true, 2: undefined, 3: false })).toEqual([
      { coffee_id: 1, liked: true }, { coffee_id: 3, liked: false },
    ]);
  });
});
