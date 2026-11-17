import { defineConfig, devices } from "playwright/test";

export default defineConfig({
  tsconfig: "./tsconfig.json",
  testDir: "../../tests/web",
  outputDir: process.env.CARESS_TEST_OUTPUT ?? "/tmp/caress-playwright-results",
  fullyParallel: true,
  use: { baseURL: "http://127.0.0.1:19308", trace: "retain-on-failure" },
  projects: [
    { name: "light", use: { ...devices["Desktop Chrome"], colorScheme: "light" } },
    { name: "dark", use: { ...devices["Desktop Chrome"], colorScheme: "dark" } },
  ],
  webServer: { command: "bun ../../tests/web/server.ts", url: "http://127.0.0.1:19308/", reuseExistingServer: false },
});
