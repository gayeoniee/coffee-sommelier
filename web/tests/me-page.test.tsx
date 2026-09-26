import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import MePage from "@/app/me/page";

afterEach(() => vi.unstubAllGlobals());

function sessionResponse() {
  return new Response(JSON.stringify({ user_id: "u", new: false, has_profile: true }), { status: 200 });
}

const profile = {
  caffeine_rule: "any", milk_ok: true, acidity: 3, body: 3, sweetness: 3, flavor_weights: {}, n_updates: 2,
};

function meResponse(overrides: Partial<{ history: unknown[] }> = {}) {
  return new Response(JSON.stringify({
    user_id: "u", nickname: null, profile,
    // Backend returns history newest-first.
    history: overrides.history ?? [
      { snapshot: { ...profile, acidity: 3, body: 2.5, sweetness: 2 }, tasting_id: 2, created_at: "2026-01-02" },
      { snapshot: { ...profile, acidity: 2, body: 2, sweetness: 2 }, tasting_id: 1, created_at: "2026-01-01" },
    ],
    tastings: [],
  }), { status: 200 });
}

describe("MePage", () => {
  it("shows a warn-styled error and retry button when /me fails, and reloads on click", async () => {
    let meCalls = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/session") return Promise.resolve(sessionResponse());
      if (url === "/api/me") {
        meCalls += 1;
        if (meCalls === 1) return Promise.resolve(new Response(JSON.stringify({ detail: "불러오지 못했어요" }), { status: 500 }));
        return Promise.resolve(meResponse());
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MePage />);
    const errorText = await screen.findByText("불러오지 못했어요");
    expect(errorText).toHaveClass("text-warn");
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(screen.getByText("내 취향")).toBeInTheDocument());
  });

  it("shows the guest-data notice, a chronological taste-change table, and styled nickname save feedback", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/session") return Promise.resolve(sessionResponse());
      if (url === "/api/me" && (!init || !init.method || init.method === "GET")) return Promise.resolve(meResponse());
      if (url === "/api/me/nickname") return Promise.resolve(new Response(JSON.stringify({ nickname: "가연" }), { status: 200 }));
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MePage />);
    expect(await screen.findByText(/게스트 기록은 이 브라우저에만 저장돼요/)).toBeInTheDocument();

    const table = screen.getByRole("table");
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3); // header + 2 snapshots
    expect(rows[0]).toHaveTextContent("산미");
    expect(rows[0]).toHaveTextContent("바디");
    expect(rows[0]).toHaveTextContent("단맛");
    // oldest (acidity 2) before newest (acidity 3)
    expect(rows[1]).toHaveTextContent("2.0");
    expect(rows[2]).toHaveTextContent("3.0");
    expect(table).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("닉네임"), "가연");
    await userEvent.click(screen.getByRole("button", { name: "저장" }));
    const success = await screen.findByText("저장했어요");
    expect(success).toHaveClass("text-leaf");
  });

  it("shows a warn-styled message when saving the nickname fails", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/session") return Promise.resolve(sessionResponse());
      if (url === "/api/me" && (!init || !init.method || init.method === "GET")) return Promise.resolve(meResponse());
      if (url === "/api/me/nickname") return Promise.resolve(new Response(JSON.stringify({ detail: "저장하지 못했어요" }), { status: 500 }));
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MePage />);
    await userEvent.click(await screen.findByRole("button", { name: "저장" }));
    const errorMsg = await screen.findByText("저장하지 못했어요");
    expect(errorMsg).toHaveClass("text-warn");
  });
});
