import { expect, test } from "@playwright/test";
import { signIn } from "./support/auth";
import { api } from "./support/cleanup";

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

test.describe("jobs: settings", () => {
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

  test("the Settings tab shows every group and saves", async ({ page }) => {
    await signIn(page, "owner", "/settings");
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const sections = page.getByRole("navigation", { name: "Settings sections" });
    for (const name of [
      "CVs & LinkedIn profile",
      "Your extra skills",
      "Scoring",
      "Follow-ups, kits and display",
      "Experience not on your CV",
      "LinkedIn connections",
    ]) {
      await expect(sections.getByRole("link", { name })).toBeVisible();
    }
    for (const card of ["Technical CV", "Leadership CV", "LinkedIn profile PDF"]) {
      await expect(page.getByRole("article", { name: card })).toBeVisible();
    }
    const form = page.getByRole("form", { name: "Settings" });
    for (const group of [
      "Your extra skills",
      "Scoring thresholds",
      "Scoring weights",
      "Pay floors",
      "Work modes and locations",
      "Follow-ups",
      "Kits",
      "Campaign and archive",
      "Display",
      "LLM budget",
    ]) {
      await expect(form.getByRole("group", { name: group })).toBeVisible();
    }
    // Saving what was loaded changes nothing but the version.
    await form.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved." })).toBeVisible();
  });

  test("users don't see the LLM budget", async ({ page }) => {
    await signIn(page, "tester", "/settings");
    const form = page.getByRole("form", { name: "Settings" });
    await expect(form.getByRole("group", { name: "Your extra skills" })).toBeVisible();
    await expect(form.getByRole("group", { name: "LLM budget" })).toHaveCount(0);
  });

  test("the weights must total 100 before saving", async ({ page }) => {
    await signIn(page, "owner", "/settings");
    const form = page.getByRole("form", { name: "Settings" });
    const skills = form.getByLabel("Skills", { exact: true });
    const before = await skills.inputValue();
    await skills.fill(String(Number(before) + 1));
    // The live total line (the field hint also mentions "must total 100").
    await expect(form.getByText(/^Total \d+: must total 100$/)).toBeVisible();
    await form.getByRole("button", { name: "Save settings" }).click();
    await expect(form.getByText(/Weights must total 100/)).toBeVisible();
    await expect(skills).toBeFocused();
    await skills.fill(before);
    await expect(form.getByText("✓ Total 100 of 100")).toBeVisible();
  });

  test("add, edit and delete experience not on your CV", async ({ page }) => {
    const requirement = `E2E mentoring ${Date.now()}`;
    await signIn(page, "tester", "/settings");
    try {
      await page
        .getByRole("navigation", { name: "Settings sections" })
        .getByRole("link", { name: "Experience not on your CV" })
        .click();
      const add = page.getByRole("form", { name: "Add one" });
      await add.getByLabel("Requirement it answers").fill(requirement);
      // The first real choice: a role from the CVs, or "Personal project" when there are none.
      await add.getByLabel("Where").selectOption({ index: 1 });
      await add.getByLabel("What you did there").fill("Coached two junior analysts.");
      await add.getByRole("button", { name: "Add", exact: true }).click();
      const record = page.getByRole("form", { name: requirement });
      await expect(record.getByLabel("Requirement it answers")).toHaveValue(requirement);
      await expect(record.getByText(/^E\d+$/)).toBeVisible();

      await record.getByLabel("What you did there").fill("Coached three junior analysts.");
      await record.getByRole("button", { name: "Save", exact: true }).click();
      await expect(page.getByText(`Saved ${requirement}.`)).toBeAttached();
      await page.reload();
      await expect(record.getByLabel("What you did there")).toHaveValue(
        "Coached three junior analysts.",
      );

      await record.getByRole("button", { name: "Delete", exact: true }).click();
      await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
      await expect(record).toHaveCount(0);
    } finally {
      // Leave no record behind if a step failed.
      const res = await api("/experience", {}, "tester");
      if (res.ok) {
        const { items } = (await res.json()) as {
          items: { id: string; requirement: string; version: number }[];
        };
        for (const r of items.filter((i) => i.requirement === requirement)) {
          await api(`/experience/${r.id}?version=${r.version}`, { method: "DELETE" }, "tester");
        }
      }
    }
  });

  test("LinkedIn connections explains the export and guards Remove all", async ({ page }) => {
    await signIn(page, "tester", "/settings");
    const section = page.getByRole("region", { name: "LinkedIn connections" });
    await expect(section.getByText(/Get a copy of your data/)).toBeVisible();
    await expect(section.getByLabel("Connections.csv")).toBeVisible();
    await expect(section.getByText(/You have \d[\d,]* contacts?\./)).toBeVisible();
    const removeAll = section.getByRole("button", { name: "Remove all contacts…" });
    if (await removeAll.isEnabled()) {
      await removeAll.click();
      await expect(section.getByRole("button", { name: /^Remove all \d/ })).toBeDisabled();
      await section.getByRole("button", { name: "Cancel" }).click();
    }
  });
});
