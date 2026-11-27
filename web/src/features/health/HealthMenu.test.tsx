import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, renderWithApi, TESTER } from "@/lib/testing";
import { HealthMenu } from "./HealthMenu";

// Mocks typed by the spec's schemas (getHealthSummary, getLlmSpend).
const HEALTH: Schemas["HealthSummary"] = {
  jobs: [
    {
      name: "feed_replay",
      last_run_at: "2026-11-27T08:00:00Z",
      last_status: "ok",
      next_run_at: "2026-11-27T08:15:00Z",
    },
    { name: "digest", last_run_at: "2026-11-27T07:00:00Z", last_status: "ok", next_run_at: null },
  ],
  llm: {
    spend_today_aud: 1.25,
    spend_month_aud: 12.4,
    budget_month_aud: 50,
    calls_today: 42,
    fallback_rate_7d: 0.05,
    by_feature: [{ feature: "kit", provider: "vertex-claude", calls: 4, spend_aud: 2 }],
  },
  site: {
    page_views_7d: 1200,
    sessions_7d: 80,
    web_vitals_p75: [
      { metric: "LCP", value: 3000, budget: 2500 },
      { metric: "CLS", value: 0.02, budget: 0.1 },
    ],
  },
  as_of: "2026-11-27T08:05:00Z",
};

const SPEND: Schemas["LlmSpendSeries"] = {
  items: [
    {
      period_start: "2026-11-17",
      feature: "kit",
      provider: "vertex-claude",
      calls: 4,
      cost_aud: 2,
    },
    {
      period_start: "2026-11-17",
      feature: "score",
      provider: "vertex-gemini",
      calls: 10,
      cost_aud: 1.5,
    },
  ],
};

let healthCalls = 0;
const server = setupServer();
beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  server.listen({ onUnhandledRequest: "error" });
});
beforeEach(() => {
  healthCalls = 0;
  server.use(
    http.get(`${API}/health/summary`, () => {
      healthCalls += 1;
      return HttpResponse.json(HEALTH);
    }),
    http.get(`${API}/dashboard/llm-spend`, () => HttpResponse.json(SPEND)),
    http.get(`${API}/scan/status`, () => HttpResponse.json(SCAN)),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// getScanStatus: the hidden pool's size for the Scheduled jobs section.
const SCAN: Schemas["ScanStatus"] = {
  last_run: null,
  next_run_at: null,
  interval_minutes: 15,
  pool_remaining: 40,
  running: false,
};

describe("HealthMenu", () => {
  it("shows how many roles are left in the scan pool", async () => {
    renderWithApi(<HealthMenu maintainer />);
    await userEvent.click(screen.getByRole("button", { name: "Health" }));
    const panel = screen.getByRole("region", { name: "Health" });
    expect(await within(panel).findByText("Pool: 40 roles left")).toBeInTheDocument();
  });

  it("is a disclosure button that controls the panel", async () => {
    renderWithApi(<HealthMenu maintainer />);
    const toggle = screen.getByRole("button", { name: "Health" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const panelId = toggle.getAttribute("aria-controls") ?? "";
    expect(document.getElementById(panelId)).not.toBeVisible();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(panelId)).toBeVisible();
    await userEvent.keyboard("{Escape}");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("shows jobs, LLM usage with the spend charts, and site usage", async () => {
    renderWithApi(<HealthMenu maintainer />);
    await userEvent.click(screen.getByRole("button", { name: "Health" }));
    const panel = screen.getByRole("region", { name: "Health" });
    const jobs = await within(panel).findByRole("table", { name: "Scheduled jobs" });
    const feed = within(jobs).getByRole("row", { name: /Feed replay/ });
    expect(feed).toHaveTextContent("2026-11-27 08:00 UTC");
    expect(feed).toHaveTextContent("OK");
    expect(feed).toHaveTextContent("2026-11-27 08:15 UTC");
    const llm = within(panel).getByRole("region", { name: "LLM usage" });
    expect(within(llm).getByText("Spend today (AUD)").nextElementSibling).toHaveTextContent(
      "$1.25",
    );
    expect(within(llm).getByText("Spend this month (AUD)").nextElementSibling).toHaveTextContent(
      "$12.40 of $50.00 (25%)",
    );
    expect(panel).toHaveTextContent("42");
    expect(panel).toHaveTextContent("5%");
    expect(
      await within(panel).findByRole("figure", { name: "LLM spend by feature" }),
    ).toBeInTheDocument();
    const byProvider = await within(panel).findByRole("figure", { name: "LLM spend by provider" });
    expect(byProvider).toHaveTextContent("vertex-claude $2.00 over 4 calls");
    expect(
      within(byProvider).getByRole("columnheader", { name: "Spend (AUD)", hidden: true }),
    ).toBeInTheDocument();
    expect(panel).toHaveTextContent("1,200");
    const vitals = within(panel).getByRole("table", { name: "Web Vitals p75 against budget" });
    expect(within(vitals).getByRole("row", { name: /LCP/ })).toHaveTextContent("Over budget");
    expect(within(vitals).getByRole("row", { name: /CLS/ })).toHaveTextContent("Within budget");
  });

  it("reports job health on the status dot", async () => {
    renderWithApi(<HealthMenu maintainer />);
    expect(await screen.findByText("Scheduled jobs: all OK")).toBeInTheDocument();
  });

  it("turns amber when a job failed or never ran", async () => {
    server.use(
      http.get(`${API}/health/summary`, () =>
        HttpResponse.json({
          ...HEALTH,
          jobs: [...HEALTH.jobs, { name: "watchdog", last_status: "never" }],
        }),
      ),
    );
    renderWithApi(<HealthMenu maintainer />);
    expect(await screen.findByText("Scheduled jobs: 1 needs attention")).toBeInTheDocument();
  });

  it("shows a tester only the maintainers note, without calling the API", async () => {
    renderWithApi(<HealthMenu maintainer={false} />, { email: TESTER });
    await userEvent.click(screen.getByRole("button", { name: "Health" }));
    const panel = screen.getByRole("region", { name: "Health" });
    expect(panel).toHaveTextContent("Health details are available to maintainers.");
    expect(within(panel).queryByRole("table")).toBeNull();
    await waitFor(() => expect(healthCalls).toBe(0));
  });
});
