import { describe, expect, it } from "vitest";

describe("toolchain", () => {
  it("runs vitest with jsdom", () => {
    expect(typeof document).toBe("object");
  });
});
