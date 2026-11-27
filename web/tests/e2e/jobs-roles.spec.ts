import { expect, type Page, test } from "@playwright/test";
import { signIn } from "./support/auth";
import { Created } from "./support/cleanup";

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

async function signInAt(page: Page, path: string) {
  await signIn(page, "owner", path);
  await expect(page).toHaveURL(new RegExp(`${path.replace(/\//g, "\\/")}$`));
}

test.describe("jobs: roles and contacts CRUD", () => {
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
      `backend not runnable: web ${web ? "up" : "down"} at ${BASE_URL}, api ${api ? "up" : "down"} at ${API_HEALTH_URL}. Start scripts/dev.sh.`,
    );
  });

  test("the Age filter is kept in the URL across a reload", async ({ page }) => {
    await signInAt(page, "/roles");
    const age = page.getByRole("combobox", { name: "Age" });
    await age.selectOption({ label: "Last 7 days" });
    await expect(page).toHaveURL(/\/roles\?max_age_days=7$/);
    await page.reload();
    await expect(page.getByRole("combobox", { name: "Age" })).toHaveValue("7");
    await page.getByRole("combobox", { name: "Age" }).selectOption({ label: "Any" });
    await expect(page).toHaveURL(/\/roles$/);
  });

  test("import, view, edit, change status and delete a role", async ({ page }) => {
    const title = `E2E role ${Date.now()}`;
    await signInAt(page, "/roles/new");
    await page.getByLabel("Title (required)").fill(title);
    await page.getByLabel("Company").fill("Acme Pty Ltd");
    await page.getByLabel("Ad text").fill("Synthetic ad text for an end-to-end test.");
    await page.getByRole("button", { name: "Import role" }).click();

    await expect(page).toHaveURL(/\/roles\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

    await page.getByRole("button", { name: "Edit role" }).click();
    await page.getByLabel("Notes").fill("Edited by e2e");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Edited by e2e")).toBeVisible();

    await page.getByRole("combobox", { name: "Status" }).selectOption("applied");
    await page.getByRole("button", { name: "Update status" }).click();
    await expect(page.getByRole("status", { name: "Status update" })).toHaveText(
      "Status changed to Applied",
    );

    await page.reload();
    await expect(page.getByText("Edited by e2e")).toBeVisible();

    await page.getByRole("button", { name: "Delete role" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Yes, delete" }).click();
    await expect(page).toHaveURL(/\/roles$/);
    await page.getByRole("searchbox", { name: "Search title or company" }).fill(title);
    await expect(page.getByRole("link", { name: title })).toHaveCount(0);
  });

  test("create, edit and delete a contact", async ({ page }) => {
    const first = `E2E${Date.now()}`;
    await signInAt(page, "/contacts");
    await page.getByRole("button", { name: "Add contact" }).click();
    const form = page.getByRole("form", { name: "New contact" });
    await form.getByLabel("First name (required)").fill(first);
    await form.getByLabel("Last name").fill("Example");
    await form.getByRole("button", { name: "Save contact" }).click();

    const name = `${first} Example`;
    await expect(page.getByRole("rowheader", { name })).toBeVisible();
    await page.getByRole("button", { name: `Edit ${name}` }).click();
    const edit = page.getByRole("form", { name: `Edit ${name}` });
    await edit.getByLabel("Company").fill("Globex");
    await edit.getByRole("button", { name: "Save contact" }).click();
    await expect(page.getByRole("row", { name: new RegExp(name) })).toContainText("Globex");

    await page.getByRole("button", { name: `Delete ${name}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Yes, delete" }).click();
    await expect(page.getByRole("rowheader", { name })).toHaveCount(0);
  });
});
