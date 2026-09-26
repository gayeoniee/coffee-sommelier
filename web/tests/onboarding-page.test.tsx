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
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByRole("button", { name: "저장" })).toBeInTheDocument();
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
