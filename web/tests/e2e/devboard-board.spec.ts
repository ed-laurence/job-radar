import { expect, type Page, test } from "@playwright/test";
import { signIn } from "./support/auth";
import { Created } from "./support/cleanup";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const API_HEALTH_URL = process.env.E2E_API_HEALTH_URL ?? "http://127.0.0.1:8000/healthz";
// Offline replay triage is quick; a live model can take longer.
const TRIAGE_TIMEOUT_MS = 90_000;

/**
 * A unique, letters-and-digits suffix. A bare Date.now() is 13 digits, which the server's PII
 * redaction treats as a phone number and replaces with "[redacted]" in the saved title.
 */
function stamp(): string {
  return Date.now().toString(36);
}

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function signInAsOwner(page: Page) {
  await signIn(page, "owner", "/dev-board");
  await expect(page.getByRole("heading", { level: 1, name: "Dev board" })).toBeVisible();
}

function column(page: Page, label: string) {
  return page.getByRole("region", { name: label, exact: true });
}

async function createTask(page: Page, title: string) {
  await page.getByRole("button", { name: "New task" }).click();
  const drawer = page.getByRole("dialog", { name: "New task" });
  await drawer.getByLabel("Title").fill(title);
  await drawer.getByLabel("Description").fill("Show a hint when the contacts list is empty.");
  await drawer.getByRole("button", { name: "Create task" }).click();
  // Several live regions exist (ours, dnd-kit's, Next's route announcer); match by text.
  await expect(page.getByText(`Created “${title}” in Backlog.`, { exact: false })).toBeVisible();
}

/**
 * Waits for triage (a system move) to land the card in Triaged or Out of scope.
 * Asserting only "absent from Backlog" passed vacuously while the board was still loading after
 * a reload, so callers then misread which column the card was in (found in e2e).
 */
async function waitForTriage(page: Page, title: string) {
  await expect(async () => {
    await page.reload();
    const landed = column(page, "Triaged")
      .getByText(title)
      .or(column(page, "Out of scope").getByText(title));
    await expect(landed.first()).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: TRIAGE_TIMEOUT_MS });
}

/** Picks a card up with the keyboard, presses arrows, and drops it. */
async function keyboardMove(page: Page, title: string, keys: string[]) {
  const card = page.getByRole("article", { name: title, exact: true });
  await card.focus();
  await page.keyboard.press("Space");
  for (const key of keys) await page.keyboard.press(key);
  await page.keyboard.press("Space");
}

test.describe("dev board", () => {
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

  test("creates a task, triage moves it and its steps stream", async ({ page }) => {
    const title = `E2E empty state ${stamp()}`;
    await signInAsOwner(page);
    await createTask(page, title);
    // The announcement above proves it was created in Backlog. Offline triage can finish within
    // milliseconds, so only assert the card is on the board, not which column it is in yet.
    await expect(page.getByRole("link", { name: title })).toBeVisible();

    // Open the card: the triage node streams (or has streamed) its steps.
    await page.getByRole("link", { name: title }).click();
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    const triageNode = page.getByRole("listitem", { name: /^Triage/ }).first();
    await expect(triageNode).toBeVisible({ timeout: TRIAGE_TIMEOUT_MS });
    const show = triageNode.getByRole("button", { name: "Show steps" });
    if (await show.isVisible()) await show.click();
    const log = triageNode.getByRole("log");
    await expect(
      log.getByRole("listitem").filter({ hasNotText: "Waiting for steps" }).first(),
    ).toBeVisible({
      timeout: TRIAGE_TIMEOUT_MS,
    });

    // Triage finishes: the result is shown and the card left Backlog.
    const triage = page.getByRole("region", { name: "Triage", exact: true });
    await expect(triage.getByRole("heading", { name: "Verification" })).toBeVisible({
      timeout: TRIAGE_TIMEOUT_MS,
    });
    await page.getByRole("link", { name: "← Back to the board" }).click();
    // The link is a client-side navigation: reloading before it commits reloads the detail page.
    await expect(page.getByRole("heading", { level: 1, name: "Dev board" })).toBeVisible();
    await waitForTriage(page, title);
  });

  test("rejects an illegal move with the reason and rolls back", async ({ page }) => {
    const title = `E2E illegal ${stamp()}`;
    await signInAsOwner(page);
    await createTask(page, title);
    await waitForTriage(page, title);
    const from = (await column(page, "Triaged").getByText(title).count())
      ? "Triaged"
      : "Out of scope";

    // Drag with the pointer onto Done: never a user move. The board scrolls sideways (11 columns
    // are wider than the viewport), so Done and the card are never on screen together: hold the
    // card at the board's right edge until auto-scroll brings Done into view, as a user would.
    // The whole card is the drag source.
    const card = page.getByRole("article", { name: title, exact: true });
    const target = column(page, "Done");
    await card.scrollIntoViewIfNeeded();
    const start = await card.boundingBox();
    const viewport = page.viewportSize();
    if (!start || !viewport) throw new Error("board not laid out");
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(start.x + 20, start.y + 20, { steps: 5 });
    const edgeX = viewport.width - 4;
    await page.mouse.move(edgeX, start.y + 20, { steps: 15 });
    let nudge = 0;
    await expect
      .poll(async () => {
        nudge = 1 - nudge;
        await page.mouse.move(edgeX - nudge, start.y + 20);
        const box = await target.boundingBox();
        return box !== null && box.x + box.width <= viewport.width;
      })
      .toBe(true);
    const box = await target.boundingBox();
    if (!box) throw new Error("Done column not laid out");
    await page.mouse.move(box.x + box.width / 2, box.y + 40, { steps: 10 });
    await page.mouse.up();

    if (from === "Triaged") {
      await expect(page.getByText(`Can't move “${title}”`, { exact: false })).toBeVisible();
    }
    await expect(column(page, from).getByText(title)).toBeVisible();
    await expect(column(page, "Done").getByText(title)).toHaveCount(0);
  });

  test("moves a card with the keyboard and announces it", async ({ page }) => {
    const title = `E2E keyboard ${stamp()}`;
    await signInAsOwner(page);
    await createTask(page, title);
    await waitForTriage(page, title);
    test.skip(
      (await column(page, "Triaged").getByText(title).count()) === 0,
      "triage marked the sample task out of scope; the keyboard move needs a triaged card",
    );

    // Triaged → Backlog ("back out") is a legal user move.
    await keyboardMove(page, title, ["ArrowLeft"]);
    await expect(page.getByText(`Dropped “${title}” in Backlog. Saving…`)).toBeAttached();
    await expect(page.getByText(`Moved “${title}” to Backlog.`)).toBeVisible();
    await expect(column(page, "Backlog").getByText(title)).toBeVisible();
  });
});
