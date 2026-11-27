import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the app already started by scripts/dev.sh (offline mode). No webServer here:
 * the journeys need the api too, and dev.sh owns starting both.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  // One dev server + embedded Postgres behind it: more workers just queue on the 5-connection
  // pool and turn timing into flakiness (a 230 ms endpoint took 4 s under 6 workers).
  workers: Number(process.env.E2E_WORKERS ?? 2),
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
