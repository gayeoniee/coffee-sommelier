import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SwRegister from "@/components/SwRegister";

// SwRegister only registers/updates the service worker in production, so every test runs
// with NODE_ENV forced to "production" and a fake navigator.serviceWorker. `updateCalls` is a
// plain counter rather than a vi.fn() mock so it can be invoked directly without fighting
// vitest 5's Mock<Procedure | Constructable> type.
describe("SwRegister", () => {
  let registerMock: ReturnType<typeof vi.fn>;
  let updateCalls = 0;
  let resolveRegister!: (registration: { update: () => void }) => void;

  const registration = { update: () => { updateCalls += 1; } };

  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "production");
    updateCalls = 0;
    registerMock = vi.fn(
      () => new Promise<{ update: () => void }>((resolve) => { resolveRegister = resolve; })
    );
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register: registerMock },
      configurable: true,
    });
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    Object.defineProperty(navigator, "serviceWorker", { value: undefined, configurable: true });
  });

  it("registers /sw.js and calls update() once registration resolves (page-load refresh)", async () => {
    render(<SwRegister />);
    expect(registerMock).toHaveBeenCalledWith("/sw.js");
    expect(updateCalls).toBe(0);
    await act(async () => {
      resolveRegister(registration);
    });
    expect(updateCalls).toBe(1);
  });

  it("calls update() again when the tab becomes visible", async () => {
    render(<SwRegister />);
    await act(async () => {
      resolveRegister(registration);
    });
    expect(updateCalls).toBe(1);

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(updateCalls).toBe(2);
  });

  it("does not call update() when the tab becomes hidden", async () => {
    render(<SwRegister />);
    await act(async () => {
      resolveRegister(registration);
    });
    expect(updateCalls).toBe(1);

    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(updateCalls).toBe(1);
  });

  it("does nothing outside production", () => {
    vi.stubEnv("NODE_ENV", "test");
    render(<SwRegister />);
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("stops listening for visibility changes after unmount", async () => {
    const { unmount } = render(<SwRegister />);
    await act(async () => {
      resolveRegister(registration);
    });
    unmount();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(updateCalls).toBe(1);
  });
});
