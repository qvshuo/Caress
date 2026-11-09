import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: process.env.CARESS_TEST_OUTPUT ?? "/tmp/caress-playwright-results",
  fullyParallel: true,
  use: { baseURL: "http://127.0.0.1:19308", trace: "retain-on-failure" },
  projects: [
    { name: "light", use: { ...devices["Desktop Chrome"], colorScheme: "light" } },
    { name: "dark", use: { ...devices["Desktop Chrome"], colorScheme: "dark" } },
  ],
  webServer: { command: "bun tests/server.ts", url: "http://127.0.0.1:19308/", reuseExistingServer: false },
});
