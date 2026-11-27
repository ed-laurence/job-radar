import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problem, renderWithApi } from "@/lib/testing";
import { DashboardPage } from "./DashboardPage";

type Role = Schemas["Role"];

// Mocks typed by the spec's schemas, so they break when the contract changes.
const SUMMARY: Schemas["JobSummary"] = {
  to_act_on: 3,
  followups_due: 0,
  oldest_waiting_hours: 30,
  applied_last_7_days: 4,
  median_post_to_apply_hours: 5,
  tab_counts: {
    act_now: 3,
    applied: 4,
    interviewing: 1,
    passed: 2,
    rejected: 1,
    unknown: 0,
    filtered: 0,
    outreach: 5,
    feedback: 1,
  },
  as_of: "2026-11-25T01:00:00Z",
};

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

function role(over: Partial<Role> & { id: string; title: string }): Role {
  return {
    source: "feed",
    stage: "scored",
    status: "new",
    found_at: minutesAgo(600),
    rank: "a0",
    version: 3,
    created_at: minutesAgo(600),
    updated_at: minutesAgo(600),
    ...over,
  };
}

const HOT = role({
  id: "00000000-0000-4000-8000-0000000000a1",
  title: "Senior Data Engineer",
  company: "Acme Pty Ltd",
  location: "Sydney NSW",
  work_mode: "hybrid",
  pay: "$150K-$170K",
  track: "contract",
  url: "https://jobs.example.test/acme/123",
  posted_at: minutesAgo(30),
  score: 82,
  why: "Strong match on the data platform work.",
});
const MID = role({
  id: "00000000-0000-4000-8000-0000000000a2",
  title: "Analytics Lead",
  company: "Globex",
  score: 60,
});

const PLAN: Schemas["CampaignPlan"] = {
  name: "Twelve-week plan",
  start: "2026-11-02",
  end: "2027-01-24",
  total_weeks: 12,
  current_week: 4,
  week: 4,
  week_start: "2026-11-23",
  week_end: "2026-11-29",
  metrics: [
    { key: "applications", label: "Applications", actual: 3, target: 5 },
    { key: "screens", label: "Screens", actual: 1, target: 2 },
  ],
  checkpoint: { week: 4, text: "Two screens booked.", due: "2026-11-29" },
  checkpoints: [],
};

const TASKS: Schemas["CampaignTaskList"] = {
  week: 4,
  items: [
    {
      id: "recruiters",
      title: "Call two recruiters",
      when: "Mon",
      steps: "1. Pick two agencies\n2. Call them",
      links: [{ label: "Agency list", url: "https://example.test/agencies" }],
      done: false,
      progress: { metric: "recruiter_calls", actual: 1, target: 2 },
    },
    { id: "review", title: "Friday review", links: [], done: true },
  ],
};

const EVIDENCE: Schemas["RoleEvidence"] = {
  must_haves_total: 5,
  shown: 4,
  from_records: 1,
  verdict: "apply",
  summary: "Strong on data platforms; light on people leadership.",
  items: [
    { requirement: "Spark", match: "shown", source: "cv" },
    { requirement: "dbt", match: "shown", source: "records" },
    { requirement: "Team leadership", match: "missing", source: "none" },
  ],
};

const INSIGHTS: Schemas["JobInsights"] = {
  applications_per_week: [
    { week_start: "2026-11-09", applied: 2 },
    { week_start: "2026-11-16", applied: 5 },
  ],
  response_rate: 0.25,
  interview_rate: 0.1,
  offer_rate: null,
  ghost_rate: 0.4,
  median_days_to_response: 6.5,
  followup_on_time_rate: 0.8,
  by_source: [
    { key: "feed", found: 30, applied: 5, interviews: 1, rejected: 2 },
    { key: "manual", found: 4, applied: 2, interviews: 1, rejected: 0 },
  ],
  by_track: [{ key: "contract", found: 20, applied: 4, interviews: 1, rejected: 1 }],
  score_bands: [{ key: "80–100", found: 6, applied: 3, interviews: 1, rejected: 1 }],
  as_of: "2026-11-25T01:00:00Z",
};

const SETTINGS: Schemas["UserSettings"] = {
  profile: { skills: "", brief: "" },
  scoring: { keep_score: 70, borderline_score: 50, notify_score: 85, notify_mission_score: 75 },
  scoring_preferences: {
    weights: { skills: 50, track_pay: 25, location: 15, freshness: 10 },
    pay_floors: { perm_salary_aud: null, contract_day_rate_aud: null },
    work_modes: [],
    preferred_locations: [],
  },
  followups: { after_hours: 72, due_hours: 168, nudge_days: 7 },
  kits: { auto_kits: false, kits_per_run: 3, spelling: "en-AU" },
  campaign: { start_date: null },
  archive_default_days: 21,
  llm: { monthly_budget_aud: null },
  display: { timezone: "UTC" },
  version: 1,
  updated_at: null,
};

const CHART_FIXTURES: Record<string, unknown> = {
  "job-summary": SUMMARY,
  "job-insights": INSIGHTS,
  funnel: {
    items: [
      { status: "new", count: 20 },
      { status: "applied", count: 5 },
      { status: "interview", count: 2 },
    ],
  },
  "post-to-apply": {
    median_hours: 30,
    p75_hours: 60,
    items: [{ period_start: "2026-11-17", median_hours: 30, count: 5 }],
  },
  "score-distribution": {
    items: [
      { min: 60, max: 69, count: 3 },
      { min: 70, max: 79, count: 7 },
    ],
  },
  "rejection-reasons": {
    items: [
      { category: "seniority", count: 1 },
      { category: "role_filled", count: 4 },
    ],
  },
};

/** The charts that take a bucket (the insights endpoint takes only the date range). */
const MAIN_CHARTS = [
  "Application funnel",
  "Time from posting to applying",
  "Score distribution",
  "Rejection reasons",
];
const INSIGHT_CHARTS = [
  "Applications per week",
  "Outcomes by source",
  "Outcomes by track",
  "Outcomes by score band",
];

const buckets: string[] = [];
const taskCalls: string[] = [];
const activityBodies: unknown[] = [];
const archiveBodies: unknown[] = [];
const roleQueries: URLSearchParams[] = [];
const statusBodies: unknown[] = [];
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
  buckets.length = 0;
  roleQueries.length = 0;
  statusBodies.length = 0;
  taskCalls.length = 0;
  activityBodies.length = 0;
  archiveBodies.length = 0;
  server.use(
    http.get(`${API}/alerts`, () => HttpResponse.json({ items: [] })),
    http.get(`${API}/settings`, () => HttpResponse.json(SETTINGS)),
    http.get(`${API}/campaign`, () => HttpResponse.json(PLAN)),
    http.get(`${API}/campaign/tasks`, () => HttpResponse.json(TASKS)),
    http.get(`${API}/campaign/activity`, () => HttpResponse.json({ week: 4, items: [] })),
    http.put(`${API}/campaign/tasks/:taskId/done`, ({ params, request }) => {
      taskCalls.push(`PUT ${params.taskId} ${new URL(request.url).searchParams.get("week")}`);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post(`${API}/campaign/activity`, async ({ request }) => {
      const body = (await request.json()) as Schemas["CampaignActivityCreate"];
      activityBodies.push(body);
      return HttpResponse.json(
        { id: "00000000-0000-4000-8000-0000000000c1", created_at: minutesAgo(0), ...body },
        { status: 201 },
      );
    }),
    http.post(`${API}/roles/archive-older`, async ({ request }) => {
      archiveBodies.push(await request.json());
      return HttpResponse.json({ archived: 2 });
    }),
    http.get(`${API}/rejections/patterns`, () =>
      HttpResponse.json({
        total: 3,
        analysed: 2,
        categories: [{ category: "role_filled", count: 2 }],
        lessons: ["Apply within the first day."],
      }),
    ),
    http.get(`${API}/contacts`, () =>
      HttpResponse.json({
        items: [
          {
            id: "00000000-0000-4000-8000-0000000000d1",
            first_name: "Sam",
            last_name: "Example",
            company: "Initech",
            status: "drafted",
            outreach_score: 80,
            version: 1,
            created_at: minutesAgo(60),
            updated_at: minutesAgo(60),
          },
        ],
      }),
    ),
    http.get(`${API}/dashboard/:name`, ({ params, request }) => {
      const name = String(params.name);
      if (name !== "job-summary" && name !== "job-insights") {
        buckets.push(new URL(request.url).searchParams.get("bucket") ?? "");
      }
      return name in CHART_FIXTURES
        ? HttpResponse.json(CHART_FIXTURES[name] as Record<string, unknown>)
        : HttpResponse.json(problem(404, "not-found"), { status: 404 });
    }),
    http.get(`${API}/roles`, ({ request }) => {
      const q = new URL(request.url).searchParams;
      roleQueries.push(q);
      const statuses = q.getAll("status");
      const items = [HOT, MID].filter((r) => statuses.includes(r.status));
      return HttpResponse.json({ items });
    }),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

async function openFeedback() {
  await userEvent.click(await screen.findByRole("button", { name: "Feedback 1" }));
}

describe("DashboardPage", () => {
  it("polls every 30 s: roles a scheduled scan added slide in and the counts update", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const ARRIVED = role({
        id: "00000000-0000-4000-8000-0000000000a3",
        title: "Platform Engineer",
        company: "Initech",
        score: 75,
      });
      let items: Role[] = [HOT];
      let summary: Schemas["JobSummary"] = SUMMARY;
      server.use(
        http.get(`${API}/dashboard/job-summary`, () => HttpResponse.json(summary)),
        http.get(`${API}/roles`, ({ request }) => {
          const statuses = new URL(request.url).searchParams.getAll("status");
          return HttpResponse.json({ items: items.filter((r) => statuses.includes(r.status)) });
        }),
      );
      renderWithApi(<DashboardPage />);
      const first = await screen.findByRole("article", { name: "Senior Data Engineer" });
      // Cards there on the first load don't animate.
      expect(first).not.toHaveClass("arrived");
      expect(await screen.findByRole("button", { name: /^Act now 3/ })).toBeInTheDocument();

      items = [ARRIVED, HOT];
      summary = { ...SUMMARY, to_act_on: 4, tab_counts: { ...SUMMARY.tab_counts, act_now: 4 } };
      await vi.advanceTimersByTimeAsync(30_000);

      const arrived = await screen.findByRole("article", { name: "Platform Engineer" });
      expect(arrived).toHaveClass("arrived");
      expect(screen.getByRole("article", { name: "Senior Data Engineer" })).not.toHaveClass(
        "arrived",
      );
      expect(await screen.findByRole("button", { name: /^Act now 4/ })).toBeInTheDocument();
      expect(screen.getByText("1 new role arrived.")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the four headline tiles from the job summary", async () => {
    renderWithApi(<DashboardPage />);
    const kpis = await screen.findByRole("region", { name: "Headline numbers" });
    const value = (label: string) =>
      within(kpis).getByText(label).closest("div")?.querySelector("dd")?.textContent;
    expect(value("To act on")).toBe("3");
    expect(value("Oldest waiting")).toBe("30h");
    expect(value("Applied, 7 days")).toBe("4");
    expect(value("Median post→apply")).toBe("5.0 h");
  });

  it("has status toggle buttons with counts, hiding Filtered when empty", async () => {
    renderWithApi(<DashboardPage />);
    const group = await screen.findByRole("group", { name: "Status" });
    await within(group).findByRole("button", { name: "Act now 3" });
    expect(
      within(group)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "Act now 3",
      "Applied 4",
      "Interviewing 1",
      "Passed 2",
      "Rejected 1",
      "Unknown 0",
      "Outreach 5",
      "Feedback 1",
      "Campaign",
    ]);
    expect(within(group).getByRole("button", { name: "Act now 3" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows Filtered when it has roles, and filters the list by stage", async () => {
    server.use(
      http.get(`${API}/dashboard/job-summary`, () =>
        HttpResponse.json({ ...SUMMARY, tab_counts: { ...SUMMARY.tab_counts, filtered: 2 } }),
      ),
    );
    renderWithApi(<DashboardPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Filtered 2" }));
    await waitFor(() => expect(roleQueries.at(-1)?.getAll("stage")).toEqual(["filtered"]));
    expect(roleQueries.at(-1)?.getAll("status")).toEqual([]);
  });

  it("lists Act now roles as cards like job-seeker's", async () => {
    renderWithApi(<DashboardPage />);
    const card = await screen.findByRole("article", { name: "Senior Data Engineer" });
    expect(roleQueries[0]?.getAll("status")).toEqual(["new", "reviewing"]);
    expect(within(card).getByText("82")).toBeInTheDocument();
    expect(card).toHaveTextContent("Acme Pty Ltd · Sydney NSW");
    const chips = within(within(card).getByRole("list", { name: "Details" }))
      .getAllByRole("listitem")
      .map((c) => c.textContent);
    expect(chips).toEqual(["Contract", "Hybrid", "$150K-$170K", "Feed"]);
    expect(card).toHaveTextContent("Posted 30m ago");
    expect(card).toHaveTextContent("Strong match on the data platform work.");
    const open = within(card).getByRole("link", { name: /Open listing/ });
    expect(open).toHaveAttribute("href", HOT.url);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(card).getByRole("link", { name: "Write kit" })).toHaveAttribute(
      "href",
      `/roles/${HOT.id}#kit`,
    );
    expect(within(card).getByRole("link", { name: "Ask" })).toHaveAttribute(
      "href",
      `/roles/${HOT.id}#chat`,
    );
    expect(within(card).getByRole("button", { name: "Pass" })).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Back to Act now" })).toBeNull();
  });

  it("shows the status chip and Back to Act now outside Act now", async () => {
    server.use(
      http.get(`${API}/roles`, () =>
        HttpResponse.json({ items: [{ ...MID, status: "applied" as const }] }),
      ),
    );
    renderWithApi(<DashboardPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Applied 4" }));
    const card = await screen.findByRole("article", { name: "Analytics Lead" });
    expect(within(card).getByRole("list", { name: "Details" })).toHaveTextContent("Applied");
    expect(within(card).getByRole("button", { name: "Back to Act now" })).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Mark applied" })).toBeNull();
  });

  it("marks a role applied with the version it was read at", async () => {
    server.use(
      http.post(`${API}/roles/:roleId/status`, async ({ request }) => {
        const body = (await request.json()) as { status: Role["status"] };
        statusBodies.push(body);
        return HttpResponse.json({ ...HOT, status: body.status, version: 4 });
      }),
    );
    renderWithApi(<DashboardPage />);
    const card = await screen.findByRole("article", { name: "Senior Data Engineer" });
    await userEvent.click(within(card).getByRole("button", { name: "Mark applied" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "“Senior Data Engineer” marked applied.",
    );
    expect(statusBodies).toEqual([{ status: "applied", version: 3 }]);
  });

  it("explains a version conflict and refreshes the list", async () => {
    server.use(
      http.post(`${API}/roles/:roleId/status`, () =>
        HttpResponse.json(problem(409, "version-conflict", { current_version: 5 }), {
          status: 409,
          headers: { "content-type": "application/problem+json" },
        }),
      ),
    );
    renderWithApi(<DashboardPage />);
    const card = await screen.findByRole("article", { name: "Senior Data Engineer" });
    const before = roleQueries.length;
    await userEvent.click(within(card).getByRole("button", { name: "Pass" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "“Senior Data Engineer” changed elsewhere. The list has been refreshed; try again.",
    );
    await waitFor(() => expect(roleQueries.length).toBeGreaterThan(before));
  });

  it("pages with Show more", async () => {
    server.use(
      http.get(`${API}/roles`, ({ request }) => {
        const q = new URL(request.url).searchParams;
        roleQueries.push(q);
        return q.get("cursor") === "next-1"
          ? HttpResponse.json({ items: [MID] })
          : HttpResponse.json({ items: [HOT], next_cursor: "next-1" });
      }),
    );
    renderWithApi(<DashboardPage />);
    await screen.findByRole("article", { name: "Senior Data Engineer" });
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("article", { name: "Analytics Lead" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("puts job analytics in a closed disclosure right under the tiles, and rejections under Feedback", async () => {
    renderWithApi(<DashboardPage />);
    const analytics = await screen.findByRole("region", { name: "Job analytics" });
    const box = analytics.closest("details");
    expect(box).not.toHaveAttribute("open");
    // Directly under the headline tiles, above the status tabs.
    const tiles = await screen.findByRole("region", { name: "Headline numbers" });
    expect(tiles.nextElementSibling).toBe(box);
    await waitFor(() =>
      expect(box?.querySelector("summary")).toHaveTextContent(
        "Job analytics · Response rate 25% · 20 new, 5 applied, 2 interview.",
      ),
    );
    const rates = await within(analytics).findByRole("region", { name: "Outcome rates" });
    for (const [label, value] of [
      ["Response rate", "25%"],
      ["Interview rate", "10%"],
      ["Offer rate", "—"],
      ["Ghost rate", "40%"],
      ["Median days to response", "6.5 days"],
      ["Follow-ups on time", "80%"],
    ] as const) {
      expect(within(rates).getByText(label).nextElementSibling).toHaveTextContent(value);
    }
    expect(within(analytics).getByText(/Updated 25 Nov 2026/)).toBeInTheDocument();
    const bySource = await within(analytics).findByRole("table", {
      name: "Outcomes by source data",
    });
    expect(
      within(bySource)
        .getAllByRole("columnheader")
        .map((c) => c.textContent),
    ).toEqual(["Source", "Found", "Applied", "Interviews", "Rejected"]);
    expect(within(bySource).getByRole("rowheader", { name: "Job feed" })).toBeInTheDocument();
    expect(within(analytics).getByRole("figure", { name: "Outcomes by source" })).toHaveTextContent(
      "Logged by hand converts best, with 50% reaching interview.",
    );
    for (const title of [...MAIN_CHARTS, ...INSIGHT_CHARTS]) {
      const figure = await within(analytics).findByRole("figure", { name: title });
      expect(within(figure).getByRole("heading", { name: title })).toBeInTheDocument();
      expect(
        await within(figure).findByRole("table", { name: `${title} data` }),
      ).toBeInTheDocument();
    }
    expect(screen.getAllByRole("figure")).toHaveLength(MAIN_CHARTS.length + INSIGHT_CHARTS.length);
    expect(screen.queryByText(/LLM spend/)).toBeNull();
    expect(screen.queryByText(/Agent pass rate/)).toBeNull();

    await openFeedback();
    const trends = await screen.findByRole("region", { name: "Rejection trends" });
    const rejections = await within(trends).findByRole("table", {
      name: "Rejection reasons data",
    });
    expect(
      within(rejections)
        .getAllByRole("rowheader")
        .map((c) => c.textContent),
    ).toEqual(["Role filled", "Seniority"]);
  });

  it("keeps the other charts when one endpoint fails", async () => {
    server.use(
      http.get(`${API}/dashboard/score-distribution`, () =>
        HttpResponse.json(problem(500, "internal"), { status: 500 }),
      ),
    );
    renderWithApi(<DashboardPage />);
    const scores = await screen.findByRole("figure", { name: "Score distribution" });
    expect(await within(scores).findByRole("alert")).toHaveTextContent("corr-test-0001");
    expect(
      await screen.findByRole("table", { name: "Application funnel data" }),
    ).toBeInTheDocument();
  });

  it("refetches the job charts with the chosen bucket", async () => {
    renderWithApi(<DashboardPage />);
    await screen.findByRole("table", { name: "Application funnel data" });
    expect(new Set(buckets)).toEqual(new Set(["week"]));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Group by" }), "month");
    await waitFor(() =>
      expect(buckets.filter((b) => b === "month")).toHaveLength(MAIN_CHARTS.length),
    );
  });

  it("says so when a chart has no data", async () => {
    server.use(http.get(`${API}/dashboard/funnel`, () => HttpResponse.json({ items: [] })));
    renderWithApi(<DashboardPage />);
    const funnel = await screen.findByRole("figure", { name: "Application funnel" });
    expect(await within(funnel).findByText("No roles in this range.")).toBeInTheDocument();
    expect(within(funnel).queryByRole("table")).not.toBeInTheDocument();
  });
  it("shows alerts in the banner, and the follow-ups due on Applied", async () => {
    server.use(
      http.get(`${API}/alerts`, () =>
        HttpResponse.json({
          items: [{ level: "warning", title: "Follow-ups due", text: "Two applications." }],
        }),
      ),
      http.get(`${API}/dashboard/job-summary`, () =>
        HttpResponse.json({ ...SUMMARY, followups_due: 2 }),
      ),
    );
    renderWithApi(<DashboardPage />);
    const alerts = await screen.findByRole("region", { name: "Alerts" });
    await waitFor(() => expect(alerts).toHaveTextContent("Follow-ups dueTwo applications."));
    expect(
      await screen.findByRole("button", { name: "Applied 4 · 2 to follow up" }),
    ).toBeInTheDocument();
  });

  it("shows the evidence strip on Act now cards", async () => {
    server.use(
      http.get(`${API}/roles`, () =>
        HttpResponse.json({ items: [{ ...HOT, evidence: EVIDENCE }] }),
      ),
    );
    renderWithApi(<DashboardPage />);
    const card = await screen.findByRole("article", { name: "Senior Data Engineer" });
    expect(card).toHaveTextContent(
      "Apply · you show 4 of 5 must-haves (1 from your records, not on your CV)",
    );
    const items = within(within(card).getByRole("list", { name: "Must-haves" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(items).toEqual([
      "✓Spark (shown)On your CV",
      "✓dbt (shown)your records · not on CV yet",
      "✗Team leadership (missing)",
    ]);
    expect(card).toHaveTextContent("Posted 30m ago");
    expect(card).toHaveTextContent("30m left");
    expect(within(card).getByText("Notes · write a kit")).toBeInTheDocument();
  });

  it("archives Act now roles older than the chosen days, and filters by track", async () => {
    renderWithApi(<DashboardPage />);
    await screen.findByRole("article", { name: "Senior Data Engineer" });
    const days = screen.getByRole("spinbutton", { name: "Days" });
    // The default comes from Settings.
    await waitFor(() => expect(days).toHaveValue(21));
    await userEvent.clear(days);
    await userEvent.type(days, "30");
    await userEvent.click(screen.getByRole("button", { name: "Archive older than" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Archived 2 roles posted more than 30 days ago.",
    );
    expect(archiveBodies).toEqual([{ days: 30 }]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Track" }), "exec");
    await waitFor(() => expect(roleQueries.at(-1)?.get("track")).toBe("exec"));
    expect(screen.getByRole("link", { name: "+ Log an application" })).toHaveAttribute(
      "href",
      "/roles/new",
    );
  });

  it("shows the campaign plan, ticks tasks and logs activity on the Campaign tab", async () => {
    renderWithApi(<DashboardPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Campaign" }));
    const plan = await screen.findByRole("region", { name: "Twelve-week plan" });
    expect(plan).toHaveTextContent("Week 4 of 12");
    expect(plan).toHaveTextContent("1 screens");
    expect(plan).toHaveTextContent("3/5");
    expect(plan).toHaveTextContent("Checkpoint, end of week 4 (29 Nov): Two screens booked.");
    const tasks = screen.getByRole("region", { name: /Tasks · week 4/ });
    expect(await within(tasks).findByText("1 of 2 done.", { exact: false })).toBeInTheDocument();
    expect(within(tasks).getByRole("button", { name: "Previous week" })).toBeEnabled();
    await userEvent.click(
      within(tasks).getByRole("checkbox", { name: "Done: Call two recruiters" }),
    );
    await waitFor(() => expect(taskCalls).toEqual(["PUT recruiters 4"]));

    const log = screen.getByRole("region", { name: "Log activity" });
    await userEvent.selectOptions(within(log).getByRole("combobox", { name: "What" }), "screen");
    await userEvent.type(within(log).getByRole("textbox", { name: "Company" }), "Initech");
    await userEvent.click(within(log).getByRole("button", { name: "Log it" }));
    expect(await within(log).findByRole("status")).toHaveTextContent("Logged: Screen.");
    expect(activityBodies).toEqual([
      expect.objectContaining({ kind: "screen", company: "Initech" }),
    ]);
  });

  it("shows outreach contacts and rejection patterns on their tabs", async () => {
    renderWithApi(<DashboardPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Outreach 5" }));
    const outreach = await screen.findByRole("region", { name: "Outreach" });
    expect(await within(outreach).findByText("Sam Example")).toBeInTheDocument();
    await openFeedback();
    const feedback = await screen.findByRole("region", { name: "Why you were turned down" });
    expect(feedback).toHaveTextContent("2 of 3 rejections analysed");
    expect(feedback).toHaveTextContent("Apply within the first day.");
  });
});
