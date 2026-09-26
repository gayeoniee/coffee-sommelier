import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// NEXT_PUBLIC_* env vars are inlined at build time by Next.js, but process.env is read at module
// evaluation time in this Vitest setup, so each case re-imports the component fresh after stubbing.
afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("VariantBanner", () => {
  it("renders nothing when NEXT_PUBLIC_VARIANT is not set", async () => {
    vi.stubEnv("NEXT_PUBLIC_VARIANT", "");
    const { default: VariantBanner } = await import("@/components/VariantBanner");
    render(<VariantBanner />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("renders the submission notice when NEXT_PUBLIC_VARIANT=open", async () => {
    vi.stubEnv("NEXT_PUBLIC_VARIANT", "open");
    const { default: VariantBanner } = await import("@/components/VariantBanner");
    render(<VariantBanner />);
    expect(screen.getByRole("note")).toHaveTextContent(
      "공모전 제출본 · 오픈 데이터 + 국내 로스터리 사실정보 (coffeereview 미포함)"
    );
  });
});
