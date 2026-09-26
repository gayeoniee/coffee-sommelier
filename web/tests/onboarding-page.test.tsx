import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: push }), useSearchParams: () => new URLSearchParams() }));

import { OnboardingFlow } from "@/components/OnboardingFlow";

afterEach(() => { vi.unstubAllGlobals(); push.mockReset(); });

function route(url: string, init?: RequestInit) {
  if (url === "/api/onboarding/samples" && (!init || !init.method || init.method === "GET")) {
    return new Response(JSON.stringify([
      { coffee_id: 1, name: "Ethiopia Washed", tags: ["lemon"], tags_ko: ["레몬"], description: "레몬 향이 나는 원두" },
      { coffee_id: 2, name: "Brazil", tags: ["chocolate"], tags_ko: ["초콜릿"], description: "초콜릿 향이 나는 원두" },
    ]));
  }
  if (url === "/api/me" && (!init || !init.method || init.method === "GET")) {
    return new Response(JSON.stringify({ user_id: "u", nickname: null, profile: null, history: [], tastings: [] }));
  }
  return new Response(JSON.stringify({ profile: {} }));
}

describe("onboarding flow", () => {
  it("walks 3 steps and saves profile then answered samples", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => Promise.resolve(route(url, init)));
    vi.stubGlobal("fetch", fetchMock);
    render(<OnboardingFlow redo={false} />);
    await userEvent.click(screen.getByRole("radio", { name: "디카페인만" }));
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    await userEvent.click(screen.getByRole("button", { name: "과일" }));
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    await userEvent.click(await screen.findByRole("button", { name: "Ethiopia Washed 좋아요" }));
    await userEvent.click(screen.getByRole("button", { name: "시작하기" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    const calls = fetchMock.mock.calls.map(([u, i]) => [u, i?.method ?? "GET", i?.body ? JSON.parse(i.body as string) : null]);
    expect(calls).toContainEqual(["/api/me/profile", "PUT", expect.objectContaining({ caffeine_rule: "decaf_only", flavor_likes: ["fruity"] })]);
    expect(calls).toContainEqual(["/api/onboarding/samples", "POST", [{ coffee_id: 1, liked: true }]]);
    const putIdx = calls.findIndex((c) => c[1] === "PUT");
    const postIdx = calls.findIndex((c) => c[0] === "/api/onboarding/samples" && c[1] === "POST");
    expect(putIdx).toBeLessThan(postIdx);
  });

  it("redo skips the samples step", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => Promise.resolve(route(url, init))));
    render(<OnboardingFlow redo />);
    await userEvent.click(await screen.findByRole("button", { name: "다음" }));
    expect(screen.getByRole("button", { name: "저장" })).toBeInTheDocument();
  });
  it("redo prefills the draft from the current profile and saves it unchanged", async () => {
    const profile = {
      caffeine_rule: "decaf_only", milk_ok: false, acidity: 4.5, body: 2, sweetness: 3.5,
      flavor_weights: { fruity: 0.6, floral: 0.1, sweet: 0.9 }, n_updates: 2,
    };
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/me" && (!init || !init.method || init.method === "GET")) {
        return Promise.resolve(new Response(JSON.stringify({
          user_id: "u", nickname: null, profile, history: [], tastings: [],
        })));
      }
      return Promise.resolve(route(url, init));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<OnboardingFlow redo />);
    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
    await screen.findByRole("radio", { name: "디카페인만" });
    expect(screen.getByRole("radio", { name: "디카페인만" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByLabelText("산미")).toHaveValue("4.5");
    expect(screen.getByLabelText("바디")).toHaveValue("2");
    expect(screen.getByLabelText("단맛")).toHaveValue("3.5");
    expect(screen.getByRole("button", { name: "과일" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "단맛" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "꽃" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    const putCall = fetchMock.mock.calls.find(([u, i]) => u === "/api/me/profile" && i?.method === "PUT");
    expect(putCall).toBeTruthy();
    expect(JSON.parse(putCall![1]!.body as string)).toEqual({
      caffeine_rule: "decaf_only", milk_ok: false, acidity: 4.5, body: 2, sweetness: 3.5,
      flavor_likes: expect.arrayContaining(["fruity", "sweet"]),
    });
    expect(JSON.parse(putCall![1]!.body as string).flavor_likes).toHaveLength(2);
  });

  it("a 409 from samples (already onboarded) still finishes", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => Promise.resolve(
      url === "/api/onboarding/samples" && init?.method === "POST"
        ? new Response(JSON.stringify({ detail: "이미 온보딩을 마쳤어요" }), { status: 409 })
        : route(url, init))));
    render(<OnboardingFlow redo={false} />);
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    await userEvent.click(await screen.findByRole("button", { name: "Brazil 별로" }));
    await userEvent.click(screen.getByRole("button", { name: "시작하기" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });
});
