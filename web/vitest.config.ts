import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // The query suites share one throwaway PostgreSQL database and truncate it
    // between tests, so running files in parallel would have them delete each
    // other's fixtures. The whole run takes about a second; isolating on a
    // database or schema per worker would cost more than it saves.
    fileParallelism: false,
  },
});