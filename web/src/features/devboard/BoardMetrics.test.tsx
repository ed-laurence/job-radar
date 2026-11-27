import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problem, renderWithApi } from "@/lib/testing";
import { BoardMetrics } from "./BoardMetrics";

// Mocks typed by the spec's schemas (getDevMetrics, getAgentPassRate).
const METRICS: Schemas["DevMetrics"] = {
  throughput: [
    { week_start: "2026-11-16", done: 3 },
    { week_start: "2026-11-09", done: 2 },
  ],
  wip_by_column: [
    { column: "agent_dev", count: 2 },
    { column: "human_review", count: 1 },
    { column: "done", count: 9 },
  ],
  cycle_time_by_column: [
    { column: "agent_dev", hours_p50: 3 },
    { column: "human_review", hours_p50: 20 },
  ],
  lead_time_hours_p50: 26,
  lead_time_hours_p90: 96,
  agent_pass_rate: 0.75,
  avg_review_rounds: 1.4,
  out_of_scope_rate: 0.1,
  avg_cost_per_task_usd: 0.82,
  run_outcomes: [
    { outcome: "succeeded", count: 8 },
    { outcome: "failed", count: 2 },
  ],
  blocked_count: 1,
  oldest_human_review_hours: null,
  as_of: "2026-11-27T08:00:00Z",
};

const PASS_RATE: Schemas["AgentPassRateSeries"] = {
  items: [
    { period_start: "2026-11-17", mode: "single", runs: 4, passed: 2, pass_rate: 0.5 },
    { period_start: "2026-11-17", mode: "multi", runs: 4, passed: 3, pass_rate: 0.75 },
  ],
};

const CHARTS = [
  "Throughput per week",
  "Work in progress by column",
  "Median hours per column",
  "Agent run outcomes",
  "Agent pass rate",
];

let metricCalls = 0;
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
  metricCalls = 0;
  server.use(
    http.get(`${API}/dashboard/dev-metrics`, () => {
      metricCalls += 1;
      return HttpResponse.json(METRICS);
    }),
    http.get(`${API}/dashboard/agent-pass-rate`, () => HttpResponse.json(PASS_RATE)),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

async function open() {
  renderWithApi(<BoardMetrics />);
  await userEvent.click(screen.getByText("Board metrics"));
}

describe("BoardMetrics", () => {
  it("loads nothing until opened", () => {
    renderWithApi(<BoardMetrics />);
    expect(metricCalls).toBe(0);
    expect(screen.queryByRole("figure")).toBeNull();
  });

  it("shows the flow and agent tiles", async () => {
    await open();
    await screen.findByText("Lead time p50");
    const terms = screen.getAllByRole("term");
    const value = async (label: string) =>
      terms.find((t) => t.textContent === label)?.nextElementSibling?.textContent;
    expect(await value("Lead time p50")).toBe("26.0 h");
    expect(await value("Lead time p90")).toBe("4.0 days");
    expect(await value("Agent pass rate")).toBe("75%");
    expect(await value("Avg review rounds")).toBe("1.4");
    expect(await value("Out-of-scope rate")).toBe("10%");
    expect(await value("Avg cost per task")).toBe("$0.82");
    expect(await value("Blocked")).toBe("1");
    expect(await value("Oldest in human review")).toBe("—");
  });

  it("gives every chart a text summary and a data table", async () => {
    await open();
    for (const title of CHARTS) {
      const figure = await screen.findByRole("figure", { name: title });
      expect(
        await within(figure).findByRole("table", { name: `${title} data`, hidden: true }),
      ).toBeInTheDocument();
    }
    expect(screen.getByRole("figure", { name: "Throughput per week" })).toHaveTextContent(
      "5 cards done over 2 weeks; 3 in the week of 2026-11-16.",
    );
    expect(screen.getByRole("figure", { name: "Work in progress by column" })).toHaveTextContent(
      "3 cards in progress; the most are in Agent dev (2).",
    );
    expect(screen.getByRole("figure", { name: "Median hours per column" })).toHaveTextContent(
      "Cards wait longest in Human review (median 20.0 h).",
    );
    expect(screen.getByRole("figure", { name: "Agent run outcomes" })).toHaveTextContent(
      "10 agent runs: 8 succeeded, 2 failed.",
    );
    expect(screen.getByRole("figure", { name: "Agent pass rate" })).toHaveTextContent(
      "Single agent passed 50% of 4 runs; multi-agent passed 75% of 4 runs.",
    );
  });

  it("shows the error with its reference when the metrics fail", async () => {
    server.use(
      http.get(`${API}/dashboard/dev-metrics`, () =>
        HttpResponse.json(problem(404, "not-found"), { status: 404 }),
      ),
    );
    await open();
    expect(
      await screen.findByRole("heading", { name: "Board metrics failed to load" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("figure", { name: "Agent pass rate" })).toBeInTheDocument();
  });
});
