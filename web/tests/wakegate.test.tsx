import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WakeGate from "@/components/WakeGate";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("WakeGate", () => {
  it("shows the wake-up message when the session is slow, then the app", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let resolve!: (r: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => { resolve = r; })));
    render(<WakeGate><p>앱 본문</p></WakeGate>);
    expect(screen.queryByText("앱 본문")).not.toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(2600); });
    expect(screen.getByText(/서버를 깨우는 중/)).toBeInTheDocument();
    await act(async () => {
      resolve(new Response(JSON.stringify({ user_id: "u", new: true, has_profile: true }), { status: 200 }));
    });
    expect(screen.getByText("앱 본문")).toBeInTheDocument();
  });

  it("shows an error with retry when the backend is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "서버에 연결할 수 없어요" }), { status: 502 })));
    render(<WakeGate><p>앱 본문</p></WakeGate>);
    expect(await screen.findByText("서버에 연결할 수 없어요")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });
});
