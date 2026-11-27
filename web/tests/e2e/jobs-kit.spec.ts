import { expect, test } from "@playwright/test";
import { signIn } from "./support/auth";
import { api, Created } from "./support/cleanup";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const API_HEALTH_URL = process.env.E2E_API_HEALTH_URL ?? "http://127.0.0.1:8000/healthz";

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

test.describe("jobs: kit streaming", () => {
  // Delete what each test created (UI creates are watched; API creates are added).
  let created = new Created();
  test.beforeEach(async ({ page }) => {
    created = new Created();
    await created.watch(page);
  });
  test.afterEach(async () => {
    await created.cleanup();
  });

  test.beforeAll(async () => {
    const [web, api] = await Promise.all([
      reachable(`${BASE_URL}/sign-in`),
      reachable(API_HEALTH_URL),
    ]);
    test.skip(
      !web || !api,
      `backend not runnable: web ${web ? "up" : "down"} at ${BASE_URL}, api ${api ? "up" : "down"} at ${API_HEALTH_URL}. Start scripts/dev.sh (LLM=replay).`,
    );
  });

  test("a kit streams in segment by segment and finishes", async ({ page }) => {
    await signIn(page, "owner");
    // Created through the BFF with the owner's real ID token (read in Node, never in the trace).
    const res = await api("/roles", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: "manual",
        title: `E2E kit ${Date.now().toString(36)}`,
        company: "Acme Pty Ltd",
      }),
    });
    expect(res.ok).toBe(true);
    const role = (await res.json()) as { id: string };
    created.add("roles", role.id);

    await page.goto(`/roles/${role.id}`);
    await page.getByRole("button", { name: /Generate kit|Regenerate kit/ }).click();

    const output = page.getByRole("region", { name: "Generated kit" });
    await expect(output).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Kit generated.")).toBeVisible({ timeout: 60_000 });
    await expect(output).not.toBeEmpty();
    await expect(page.getByRole("button", { name: "Stop generating" })).toHaveCount(0);
  });
});
