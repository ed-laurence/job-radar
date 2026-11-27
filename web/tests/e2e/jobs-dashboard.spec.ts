import { expect, test } from "@playwright/test";
import { signIn } from "./support/auth";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const API_HEALTH_URL = process.env.E2E_API_HEALTH_URL ?? "http://127.0.0.1:8000/healthz";

const CHARTS = [
  "Application funnel",
  "Time from posting to applying",
  "Score distribution",
  "Rejection reasons",
  "Applications per week",
  "Outcomes by source",
  "Outcomes by track",
  "Outcomes by score band",
];

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

test.describe("jobs: dashboard", () => {
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

  test("renders KPI tiles, status tabs, role cards and every job chart with a summary", async ({
    page,
  }) => {
    await signIn(page, "owner", "/dashboard");
    await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeAttached();
    const kpis = page.getByRole("region", { name: "Headline numbers" });
    await expect(kpis).toBeVisible();
    for (const label of ["To act on", "Oldest waiting", "Applied, 7 days", "Median post→apply"]) {
      await expect(kpis.getByText(label)).toBeVisible();
    }
    const tabs = page.getByRole("group", { name: "Status" });
    for (const label of [
      "Act now",
      "Applied",
      "Interviewing",
      "Passed",
      "Rejected",
      "Unknown",
      "Outreach",
      "Feedback",
    ]) {
      await expect(tabs.getByRole("button", { name: new RegExp(`^${label}`) })).toBeVisible();
    }
    await expect(tabs.getByRole("button", { name: /^Act now/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("link", { name: "+ Log an application" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Track" })).toBeVisible();
    // The campaign plan: a column beside the main one on wide screens, else the Campaign tab.
    const wide = (page.viewportSize()?.width ?? 0) >= 1180;
    if (wide) {
      await expect(page.getByRole("complementary", { name: "Campaign plan" })).toBeVisible();
    } else {
      await expect(tabs.getByRole("button", { name: /^Campaign/ })).toBeVisible();
    }
    // Job analytics: a closed disclosure directly under the tiles; rejection reasons also on Feedback.
    const analytics = page.getByRole("region", { name: "Job analytics" });
    await expect(analytics).toBeHidden();
    const next = kpis.locator("xpath=following-sibling::*[1]");
    await expect(next.locator(":scope > summary")).toContainText("Job analytics");
    await page.locator("summary", { hasText: "Job analytics" }).click();
    await expect(analytics).toBeVisible();
    await expect(analytics.getByRole("region", { name: "Outcome rates" })).toBeVisible();
    await expect(page.getByRole("figure", { name: "LLM spend by feature" })).toHaveCount(0);
    for (const title of CHARTS) {
      const figure = analytics.getByRole("figure", { name: title });
      await expect(figure.getByRole("heading", { name: title })).toBeVisible();
      await expect(figure.locator("figcaption")).not.toBeEmpty();
      await expect(figure.getByRole("alert")).toHaveCount(0);
    }
    await page
      .getByRole("region", { name: "Job analytics" })
      .getByRole("combobox", { name: "Group by" })
      .selectOption("month");
    await expect(page.getByRole("figure", { name: "Application funnel" })).toBeVisible();
    await tabs.getByRole("button", { name: /^Feedback/ }).click();
    const rejections = page
      .getByRole("region", { name: "Rejection trends" })
      .getByRole("figure", { name: "Rejection reasons" });
    await expect(rejections.getByRole("heading", { name: "Rejection reasons" })).toBeVisible();
    await expect(rejections.getByRole("alert")).toHaveCount(0);
  });
});
