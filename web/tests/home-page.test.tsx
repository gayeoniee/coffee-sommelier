import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

import Home from "@/app/page";

afterEach(() => vi.unstubAllGlobals());

function sessionResponse() {
  return new Response(JSON.stringify({ user_id: "u", new: false, has_profile: true }), { status: 200 });
}
function meResponse() {
  return new Response(JSON.stringify({ user_id: "u", nickname: null, profile: null, history: [], tastings: [] }), { status: 200 });
}

describe("Home", () => {
  it("shows a retry button when /brands fails, and refetches on click", async () => {
    let brandsCalls = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/session") return Promise.resolve(sessionResponse());
      if (url === "/api/me") return Promise.resolve(meResponse());
      if (url === "/api/brands") {
        brandsCalls += 1;
        if (brandsCalls === 1) return Promise.resolve(new Response(JSON.stringify({ detail: "오류" }), { status: 500 }));
        return Promise.resolve(new Response(JSON.stringify([{ key: "brand:sb", name: "스타벅스", decaf_available: true, decaf_surcharge_krw: null, notes: null, has_menu: true }]), { status: 200 }));
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);
    const retry = await screen.findByRole("button", { name: "다시 시도" });
    expect(screen.getByText("브랜드 목록을 불러오지 못했어요")).toBeInTheDocument();
    await userEvent.click(retry);
    await waitFor(() => expect(screen.getByRole("button", { name: "스타벅스" })).toBeInTheDocument());
  });
});
