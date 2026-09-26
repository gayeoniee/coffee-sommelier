import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import LogSheet from "@/components/LogSheet";
import type { Card } from "@/lib/types";

const card: Card = {
  key: "menu:10", name: "카페 아메리카노", brand: "스타벅스", score: 83, source: "brand_bean", confidence: "medium",
  acidity: 2, body: 3, sweetness: 2, tags: [], tags_ko: [], is_decaf: false, order_decaf: true,
  decaf_surcharge_krw: 300, caffeine_mg: 150, is_milk: false, coffee_id: null, menu_item_id: 10, violation: null,
  template: "t",
};

afterEach(() => vi.unstubAllGlobals());

describe("LogSheet", () => {
  it("requires a rating, posts the tasting and shows the change summary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      summary: "산미 선호 3.0→2.5", changes: ["산미 선호 3.0→2.5"],
      profile: { caffeine_rule: "any", milk_ok: true, acidity: 2.5, body: 3, sweetness: 3, flavor_weights: {}, n_updates: 1 },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onSaved = vi.fn();
    render(<LogSheet card={card} onClose={() => {}} onSaved={onSaved} />);
    const save = screen.getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "4점" }));
    await userEvent.type(screen.getByRole("textbox"), "산미가 너무 셌어요");
    await userEvent.click(save);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/tastings");
    expect(JSON.parse(init.body)).toEqual({ rating: 4, note: "산미가 너무 셌어요", menu_item_id: 10, order_decaf: true });
    expect(await screen.findByText("산미 선호 3.0→2.5")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ acidity: 2.5 }));
  });

  it("focuses the first star on open and closes on Escape", () => {
    const onClose = vi.fn();
    render(<LogSheet card={card} onClose={onClose} onSaved={() => {}} />);
    expect(screen.getByRole("button", { name: "1점" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
