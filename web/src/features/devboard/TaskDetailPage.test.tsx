import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, meHandler, OWNER, problemResponse, renderWithApi, TESTER } from "@/lib/testing";
import { TaskDetailPage } from "./TaskDetailPage";
import { task } from "./testing";

const track = vi.fn();
vi.mock("@/lib/AnalyticsProvider", () => ({ useAnalytics: () => ({ track }) }));
const router = { push: vi.fn(), replace: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/dev-board" }));

const TASK_ID = "00000000-0000-4000-8000-0000000000b1";
const LEAD = "00000000-0000-4000-8000-0000000000f1";
const WRITER = "00000000-0000-4000-8000-0000000000f2";
const REVIEWER = "00000000-0000-4000-8000-0000000000f3";
const encoder = new TextEncoder();

function step(runId: string, seq: number, summary: string, kind = "message"): Schemas["Step"] {
  return {
    id: `00000000-0000-4000-8000-0000000001${String(seq).padStart(2, "0")}`,
    run_id: runId,
    seq,
    kind: kind as Schemas["Step"]["kind"],
    summary,
    at: "2026-11-25T01:00:00Z",
  };
}

function frame(event: string, id: string, data: unknown): Uint8Array {
  return encoder.encode(`event: ${event}\nid: ${id}\ndata: ${JSON.stringify(data)}\n\n`);
}

let current: Schemas["Task"];
let runs: Schemas["Run"][];
let plan: Schemas["Plan"] | null;
let streams: Map<string, ReadableStreamDefaultController<Uint8Array>>;
let streamRequests: string[];

const triage: Schemas["TriageResult"] = {
  task_id: TASK_ID,
  type: "feature",
  size: "S",
  risk: "low",
  areas: ["web"],
  acceptance_criteria: [
    { test_id: "web/tests/contacts.spec.ts::shows empty state", expectation: "Shows a hint" },
  ],
  needs_human_design: false,
  in_scope: true,
  created_at: "2026-11-25T00:00:00Z",
};

const server = setupServer(
  meHandler,
  http.get(`${API}/tasks/:id`, () => HttpResponse.json(current)),
  http.get(`${API}/tasks/:id/triage`, () => HttpResponse.json(triage)),
  http.get(`${API}/tasks/:id/plan`, () =>
    plan ? HttpResponse.json(plan) : problemResponse(404, "not-found"),
  ),
  http.get(`${API}/tasks/:id/runs`, () => HttpResponse.json({ items: runs })),
  http.get(`${API}/runs/:runId/steps`, ({ params, request }) => {
    const runId = String(params.runId);
    streamRequests.push(`${runId}:${request.headers.get("authorization")}`);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        streams.set(runId, controller);
      },
    });
    return new HttpResponse(body, { headers: { "content-type": "text/event-stream" } });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  track.mockReset();
  router.push.mockReset();
  streams = new Map();
  streamRequests = [];
  current = task({
    id: TASK_ID,
    title: "Add empty state",
    body: "Show a hint when there are no contacts.",
    column: "plan_review",
    version: 4,
  });
  plan = {
    task_id: TASK_ID,
    version: 2,
    mode: "multi",
    subtasks: [
      {
        id: "s1",
        title: "Empty state component",
        agent: "web-writer",
        lease_paths: ["web/src/features/contacts/"],
      },
    ],
    acceptance_tests: ["web/tests/contacts.spec.ts::shows empty state"],
    budget: { max_usd: 3, max_minutes: 30 },
    risk: "low",
    tests_approved: false,
    plan_approved: false,
    created_at: "2026-11-25T00:00:00Z",
  };
  runs = [];
});

function push(runId: string, bytes: Uint8Array) {
  act(() => streams.get(runId)?.enqueue(bytes));
}

describe("TaskDetailPage", () => {
  it("shows the triage result and the plan", async () => {
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    expect(await screen.findByRole("heading", { level: 1, name: "Add empty state" })).toBeVisible();
    const triageSection = await screen.findByRole("region", { name: "Triage" });
    await within(triageSection).findByText("feature");
    expect(within(triageSection).getByText("web")).toBeInTheDocument();
    expect(within(triageSection).getByText(/Shows a hint/)).toBeInTheDocument();
    const planSection = screen.getByRole("region", { name: "Plan" });
    expect(await within(planSection).findByText("Multi-agent")).toBeInTheDocument();
    expect(within(planSection).getByText("web/src/features/contacts/")).toBeInTheDocument();
  });

  it("shows the out-of-scope reason from triage", async () => {
    triage.in_scope = false;
    triage.out_of_scope_reason = "Needs a new GCP service.";
    try {
      renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
      expect(await screen.findByText(/Needs a new GCP service\./)).toBeInTheDocument();
    } finally {
      triage.in_scope = true;
      delete triage.out_of_scope_reason;
    }
  });

  it("lets a maintainer approve the plan and tests with both versions", async () => {
    let approval: Schemas["PlanApproval"] | undefined;
    server.use(
      http.post(`${API}/tasks/:id/plan/approve`, async ({ request }) => {
        approval = (await request.json()) as Schemas["PlanApproval"];
        current = { ...current, column: "ready", version: 5 };
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const button = await screen.findByRole("button", { name: "Approve plan + tests" });
    await userEvent.setup().click(button);
    await waitFor(() =>
      expect(approval).toEqual({
        plan_version: 2,
        task_version: 4,
        approve_tests: true,
        approve_plan: true,
      }),
    );
    expect(await screen.findByText(/The card moved to Ready/)).toBeInTheDocument();
    expect(track).toHaveBeenCalledWith("agent_run_approved");
  });

  it("explains a stale plan approval", async () => {
    server.use(
      http.post(`${API}/tasks/:id/plan/approve`, () =>
        problemResponse(409, "version-conflict", { current_version: 3 }),
      ),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Approve plan + tests" }));
    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument();
  });

  it("hides maintainer actions from a user", async () => {
    current = { ...current, active_run_id: LEAD };
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />, { email: TESTER });
    await screen.findByText("Multi-agent");
    expect(screen.queryByRole("button", { name: "Approve plan + tests" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Kill run" })).toBeNull();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("renders the run tree and streams each active node's steps live", async () => {
    runs = [
      {
        id: REVIEWER,
        task_id: TASK_ID,
        parent_run_id: LEAD,
        kind: "review",
        agent: "gemini-reviewer",
        status: "queued",
        created_at: "2026-11-25T00:03:00Z",
      },
      {
        id: WRITER,
        task_id: TASK_ID,
        parent_run_id: LEAD,
        kind: "dev",
        agent: "web-writer",
        status: "running",
        budget_usd: 2,
        cost_usd: 0.5,
        created_at: "2026-11-25T00:02:00Z",
      },
      {
        id: LEAD,
        task_id: TASK_ID,
        kind: "dev",
        agent: "dev-lead",
        status: "running",
        budget_usd: 3,
        cost_usd: 0.75,
        created_at: "2026-11-25T00:01:00Z",
      },
    ];
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);

    const lead = await screen.findByRole("listitem", { name: "Writer: dev-lead, running" });
    const writer = within(lead).getByRole("listitem", { name: "Writer: web-writer, running" });
    const reviewer = within(lead).getByRole("listitem", {
      name: "Reviewer: gemini-reviewer, queued",
    });
    // Children in creation order: writers before reviewers.
    expect(
      within(lead)
        .getAllByRole("listitem", { name: /,/ })
        .map((n) => n.getAttribute("aria-label")),
    ).toEqual(["Writer: web-writer, running", "Reviewer: gemini-reviewer, queued"]);
    expect(within(writer).getByText("Budget: $0.50 of $2.00 (25%)")).toBeInTheDocument();
    expect(within(writer).getByText("web/src/features/contacts/")).toBeInTheDocument();
    expect(reviewer).toBeInTheDocument();

    await waitFor(() => expect(streams.has(WRITER)).toBe(true));
    expect(streamRequests).toContain(`${WRITER}:Bearer fake:${OWNER}`);
    const log = within(writer).getByRole("log", { name: "Steps: Writer: web-writer" });

    push(WRITER, frame("step", "0", step(WRITER, 0, "Reading the contacts list")));
    expect(await within(log).findByText("Reading the contacts list")).toBeInTheDocument();
    push(WRITER, frame("step", "1", { ...step(WRITER, 1, "Took lease", "lease"), tool: "lease" }));
    expect(await within(log).findByText("Took lease")).toBeInTheDocument();
    // A replayed step (same seq) is never duplicated.
    push(WRITER, frame("step", "0", step(WRITER, 0, "Reading the contacts list")));
    expect(within(log).getAllByText("Reading the contacts list")).toHaveLength(1);
  });

  it("loads a finished run's steps only when expanded", async () => {
    runs = [
      {
        id: LEAD,
        task_id: TASK_ID,
        kind: "triage",
        agent: "triage",
        status: "succeeded",
        created_at: "2026-11-25T00:01:00Z",
      },
    ];
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const toggle = await screen.findByRole("button", { name: "Show steps" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(streamRequests).toHaveLength(0);
    await userEvent.setup().click(toggle);
    expect(screen.getByRole("button", { name: "Hide steps" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await waitFor(() => expect(streams.has(LEAD)).toBe(true));
    push(LEAD, frame("step", "0", step(LEAD, 0, "Classified as a web feature")));
    push(LEAD, frame("done", "1", { status: "succeeded" }));
    expect(await screen.findByText("Classified as a web feature")).toBeInTheDocument();
  });

  it("kills the active run after confirmation", async () => {
    current = { ...current, column: "agent_dev", active_run_id: LEAD, version: 7 };
    let killed: Schemas["KillRequest"] | undefined;
    server.use(
      http.post(`${API}/tasks/:id/kill`, async ({ request }) => {
        killed = (await request.json()) as Schemas["KillRequest"];
        current = { ...current, column: "blocked", version: 8 };
        delete current.active_run_id;
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Kill run" }));
    const dialog = screen.getByRole("alertdialog", { name: "Kill the active run?" });
    expect(killed).toBeUndefined();
    await user.type(within(dialog).getByLabelText("Reason (optional)"), "Budget looks wrong");
    await user.click(within(dialog).getByRole("button", { name: "Kill run" }));
    await waitFor(() => expect(killed).toEqual({ version: 7, reason: "Budget looks wrong" }));
    expect(await screen.findByText(/Run killed/)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("edits only the changed fields with the version", async () => {
    let patch: Schemas["TaskUpdate"] | undefined;
    server.use(
      http.patch(`${API}/tasks/:id`, async ({ request }) => {
        patch = (await request.json()) as Schemas["TaskUpdate"];
        current = { ...current, ...patch, version: 5, pii_redacted: false };
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit task" });
    const title = within(drawer).getByLabelText("Title");
    await user.clear(title);
    await user.type(title, "Add empty states");
    await user.click(within(drawer).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patch).toEqual({ version: 4, title: "Add empty states" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Add empty states" }),
    ).toBeInTheDocument();
  });

  it("surfaces an edit conflict instead of overwriting", async () => {
    server.use(
      http.patch(`${API}/tasks/:id`, () =>
        problemResponse(409, "version-conflict", { current_version: 5 }),
      ),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    await user.type(screen.getByLabelText("Description"), " More.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Edit task" })).toBeInTheDocument();
  });

  it("deletes after confirmation and returns to the board", async () => {
    let deletedVersion: string | null = null;
    server.use(
      http.delete(`${API}/tasks/:id`, ({ request }) => {
        deletedVersion = new URL(request.url).searchParams.get("version");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this task?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(deletedVersion).toBeNull();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Delete task" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/dev-board"));
    expect(deletedVersion).toBe("4");
  });

  it("shows not found for a missing task", async () => {
    server.use(http.get(`${API}/tasks/:id`, () => problemResponse(404, "not-found")));
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    expect(await screen.findByText("Task not found")).toBeInTheDocument();
  });
});
