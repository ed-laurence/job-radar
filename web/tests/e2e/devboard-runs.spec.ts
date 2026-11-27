import { expect, type Page, test } from "@playwright/test";
import { signIn } from "./support/auth";
import { Created } from "./support/cleanup";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const API_HEALTH_URL = process.env.E2E_API_HEALTH_URL ?? "http://127.0.0.1:8000/healthz";
const RUN_TIMEOUT_MS = 90_000;

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

function column(page: Page, label: string) {
  return page.getByRole("region", { name: label, exact: true });
}

test.describe("dev board run tree", () => {
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
    // Needs scripts/dev.sh running web + api (AUTH=fake, LLM=replay); skipped, never passed, when either is down.
    test.skip(
      !web || !api,
      `backend not runnable: web ${web ? "up" : "down"} at ${BASE_URL}, api ${api ? "up" : "down"} at ${API_HEALTH_URL}. Start scripts/dev.sh.`,
    );
  });

  test("a maintainer starts the plan and the run tree streams steps", async ({ page }) => {
    // Base 36: a bare 13-digit Date.now() is redacted as a phone number by the server's PII filter.
    const title = `E2E run tree ${Date.now().toString(36)}`;
    await signIn(page, "owner", "/dev-board");
    await expect(page.getByRole("heading", { level: 1, name: "Dev board" })).toBeVisible();

    await page.getByRole("button", { name: "New task" }).click();
    const drawer = page.getByRole("dialog", { name: "New task" });
    await drawer.getByLabel("Title").fill(title);
    await drawer.getByLabel("Description").fill("Show a hint when the contacts list is empty.");
    await drawer.getByRole("button", { name: "Create task" }).click();

    await expect(async () => {
      await page.reload();
      // Wait for the card to land, not just leave Backlog: that passed vacuously while the board
      // was still loading after the reload, and the skip below then fired on an empty board.
      const landed = column(page, "Triaged")
        .getByText(title)
        .or(column(page, "Out of scope").getByText(title));
      await expect(landed.first()).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: RUN_TIMEOUT_MS });
    test.skip(
      (await column(page, "Triaged").getByText(title).count()) === 0,
      "triage marked the sample task out of scope; the plan run needs a triaged card",
    );

    // Keyboard: Triaged → Out of scope → Plan review (a maintainer move that starts the dev lead).
    await page.getByRole("article", { name: title, exact: true }).focus();
    await page.keyboard.press("Space");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Space");
    await expect(page.getByText(`Moved “${title}” to Plan review.`)).toBeVisible();

    await page.getByRole("link", { name: title }).click();
    const tree = page.getByRole("list", { name: "Run tree" });
    const lead = tree.getByRole("listitem", { name: /^Dev lead \(plan\)/ }).first();
    await expect(lead).toBeVisible({ timeout: RUN_TIMEOUT_MS });
    const show = lead.getByRole("button", { name: "Show steps" }).first();
    if (await show.isVisible()) await show.click();
    const steps = lead.getByRole("log").first().getByRole("listitem");
    await expect(steps.filter({ hasNotText: "Waiting for steps" }).first()).toBeVisible({
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(lead.getByText(/^Status: /).first()).toBeVisible();

    // When the plan lands, a maintainer can approve it.
    await expect(page.getByRole("button", { name: "Approve plan + tests" })).toBeVisible({
      timeout: RUN_TIMEOUT_MS,
    });
  });
});
