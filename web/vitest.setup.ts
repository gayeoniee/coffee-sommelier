import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest.config.mts does not set test.globals, so @testing-library/react's
// auto-cleanup (which looks for a global `afterEach`) never registers itself.
// Without this, DOM from one test file's renders leaks into the next test.
afterEach(() => {
  cleanup();
});
