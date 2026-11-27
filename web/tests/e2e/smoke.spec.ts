import { expect, test } from "@playwright/test";
import { signIn } from "./support/auth";

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

test.describe("smoke", () => {
  test.beforeAll(async () => {
    const [web, api] = await Promise.all([
      reachable(`${BASE_URL}/sign-in`),
      reachable(API_HEALTH_URL),
    ]);
    // Needs scripts/dev.sh running web + api (identity mode); skipped, never passed, when either is down.
    test.skip(
      !web || !api,
      `backend not runnable: web ${web ? "up" : "down"} at ${BASE_URL}, api ${api ? "up" : "down"} at ${API_HEALTH_URL}. Start scripts/dev.sh.`,
    );
  });

  test("the real sign-in page guards the app; the owner signs in and the shell renders", async ({
    page,
  }) => {
    await page.goto("/roles");
    await expect(page).toHaveURL(/\/sign-in\?next=%2Froles/);
    await expect(page.getByRole("link", { name: /sign up|create account/i })).toHaveCount(0);
    // Identity mode: email + password, no placeholder-account picker.
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toHaveAttribute("type", "password");
    await expect(page.getByRole("button", { name: /Owner \(maintainer\)/ })).toHaveCount(0);

    await signIn(page, "owner", "/roles");

    await expect(page).toHaveURL(/\/roles$/);
    const nav = page.getByRole("navigation", { name: "Main" });
    for (const label of ["Dashboard", "Roles", "Contacts", "Employers", "Dev board", "Settings"]) {
      await expect(nav.getByRole("link", { name: label })).toBeVisible();
    }
    await expect(nav.getByRole("link", { name: "Analytics" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Targets" })).toHaveCount(0);
    const health = page.getByRole("button", { name: "Health" });
    await expect(health).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("heading", { level: 1, name: "Roles" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("maintainers see the scan line and Scan now; the pool is in Health", async ({ page }) => {
    await signIn(page, "owner", "/dashboard");
    const banner = page.getByRole("banner");
    await expect(banner.getByText(/^(Last scan \d|No scans yet|Scanning now…)/)).toBeVisible();
    await expect(banner.getByRole("button", { name: /^(Scan now|Scanning…)$/ })).toBeVisible();
    await page.getByRole("button", { name: "Health" }).click();
    await expect(
      page.getByRole("region", { name: "Health" }).getByText(/^Pool: [\d,]+ roles? left$/),
    ).toBeVisible();
  });

  test("users see neither the scan line nor Scan now", async ({ page }) => {
    await signIn(page, "tester", "/dashboard");
    await expect(page.getByRole("button", { name: "Health" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^(Scan now|Scanning…)$/ })).toHaveCount(0);
  });

  test("old Analytics, Pipeline and Targets links redirect", async ({ page }) => {
    await signIn(page, "owner", "/dashboard");
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.goto("/analytics");
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.goto("/pipeline");
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.goto("/targets");
    await expect(page).toHaveURL(/\/employers$/);
    await expect(page.getByRole("heading", { level: 1, name: "Employers" })).toBeVisible();
  });
});
