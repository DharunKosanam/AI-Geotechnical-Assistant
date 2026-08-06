import { defineConfig } from "vitest/config";

// Unit tests only. The include glob is deliberately scoped to app/ so vitest
// never collects tests/*.spec.ts -- those are the user's Playwright smoke
// specs and belong to a different (excluded-from-agent-use) harness.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["app/**/*.test.{ts,tsx}"],
  },
});
