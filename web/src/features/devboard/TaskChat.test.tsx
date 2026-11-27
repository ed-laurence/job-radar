import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import {
  API,
  gatedStream,
  meHandler,
  problemResponse,
  renderWithApi,
  SSE_HEADERS,
  sseFrame,
  TESTER,
} from "@/lib/testing";
import { TaskDetailPage } from "./TaskDetailPage";
import { task } from "./testing";

const track = vi.fn();
vi.mock("@/lib/AnalyticsProvider", () => ({ useAnalytics: () => ({ track }) }));
const router = { push: vi.fn(), replace: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/dev-board" }));

const TASK_ID = "00000000-0000-4000-8000-0000000000c1";
const PROPOSAL_ID = "00000000-0000-4000-8000-0000000000d1";
const OLDER_PROPOSAL_ID = "00000000-0000-4000-8000-0000000000d0";
const REPLY_ID = "00000000-0000-4000-8000-0000000000e9";

type Message = Schemas["TaskChatMessage"];
type Proposal = Schemas["TaskChatProposal"];

const msg = (n: string, author: Message["author"], content: string, extra: Partial<Message> = {}) =>
  ({
    id: `00000000-0000-4000-8000-0000000001${n}`,
    author,
    content,
    created_at: "2026-11-26T00:00:00Z",
    ...extra,
  }) satisfies Message;

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: PROPOSAL_ID,
    task_id: TASK_ID,
    status: "pending",
    reason: "The card has no criteria an agent could test.",
    changes: {
      title: { before: "Add search", after: "Add contacts search" },
      description: {
        before: "Let users search.",
        after: "Let users search contacts by name.",
      },
      acceptance_criteria: {
        before: ["Shows a hint", "Lists matches", "Old rule"],
        after: ["Shows a hint", "Lists matches newest first", "Empty search returns everything"],
      },
    },
    base_version: 4,
    created_at: "2026-11-26T00:00:00Z",
    ...overrides,
  };
}

function step(seq: number, summary: string, tool: string): Schemas["Step"] {
  return {
    id: `00000000-0000-4000-8000-0000000002${String(seq).padStart(2, "0")}`,
    run_id: REPLY_ID,
    seq,
    kind: "tool_call",
    agent: "task-chat",
    tool,
    summary,
    at: "2026-11-26T00:00:00Z",
  };
}

let current: Schemas["Task"];
let history: Message[];

const server = setupServer(
  meHandler,
  http.get(`${API}/tasks/:id`, () => HttpResponse.json(current)),
  http.get(`${API}/tasks/:id/triage`, () => problemResponse(404, "not-found")),
  http.get(`${API}/tasks/:id/plan`, () => problemResponse(404, "not-found")),
  http.get(`${API}/tasks/:id/runs`, () => HttpResponse.json({ items: [] })),
  http.get(`${API}/tasks/:id/chat`, () => HttpResponse.json({ items: history })),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  current = task({
    id: TASK_ID,
    title: "Add search",
    body: "Let users search.\n\n## Acceptance criteria\n- Shows a hint\n- Lists matches\n- Old rule",
    column: "plan_review",
    version: 4,
  });
  history = [];
});

async function chatRegion() {
  return screen.findByRole("region", { name: "Task chat" });
}

describe("Task chat", () => {
  it("streams a reply with tool steps and a retraction, then shows the saved thread", async () => {
    history = [msg("01", "user", "Hi"), msg("02", "assistant", "Hello **there**")];
    let release = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let body: unknown;
    server.use(
      http.post(`${API}/tasks/:id/chat`, async ({ request }) => {
        body = await request.json();
        return new HttpResponse(
          gatedStream(
            [
              sseFrame("step", "0", step(0, "Read the card.", "get_task")),
              sseFrame("segment", "s0", {
                segment_id: "a",
                seq: 0,
                part: "chat",
                text: "Reading ",
              }),
              sseFrame("segment", "s1", { segment_id: "b", seq: 1, part: "chat", text: "secret" }),
              sseFrame("retract", "r1", { segment_id: "b" }),
            ],
            gate,
            [sseFrame("done", "d", { result_id: REPLY_ID })],
          ),
          { headers: SSE_HEADERS },
        );
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    expect(within(region).getByRole("heading", { name: "Discuss this task" })).toBeInTheDocument();
    const log = await within(region).findByRole("log", { name: "Conversation" });
    expect(within(log).getByText("there").tagName).toBe("STRONG");

    const user = userEvent.setup();
    const input = within(region).getByRole("textbox", { name: "Message" });
    await user.type(input, "Make it testable{Enter}");

    expect(await within(log).findByText("Read the card.")).toBeInTheDocument();
    expect(await within(log).findByText(/Reading/)).toBeInTheDocument();
    expect(await within(log).findByText("[redacted]")).toBeInTheDocument();
    expect(within(log).queryByText(/secret/)).toBeNull();
    expect(within(log).getByText("Make it testable")).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Send" })).toBeDisabled();
    expect(within(region).getByRole("button", { name: "Stop reply" })).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
    expect(within(region).getByRole("status")).toHaveTextContent("Claude is replying…");

    history = [
      ...history,
      msg("03", "user", "Make it testable"),
      msg("04", "assistant", "Reading done."),
    ];
    await act(async () => release());
    expect(await within(log).findByText("Reading done.")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(region).queryByRole("button", { name: "Stop reply" })).toBeNull(),
    );
    expect(within(region).getByRole("status")).toHaveTextContent("Claude replied.");
    expect(within(log).queryByText("Read the card.")).toBeNull();
    expect(body).toEqual({ content: "Make it testable" });
  });

  it("adds a newline with Shift+Enter instead of sending, and caps the length", async () => {
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const input = await within(region).findByRole("textbox", { name: "Message" });
    expect(input).toHaveAttribute("maxLength", "4000");
    const user = userEvent.setup();
    await user.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(input).toHaveValue("line one\nline two");
  });

  it("renders a proposed edit as before/after diffs with the reason", async () => {
    history = [
      msg("01", "user", "Tighten this up"),
      msg("02", "assistant", "Here is an edit.", { proposal: proposal() }),
    ];
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const card = await within(region).findByRole("group", { name: "Proposed edit" });

    const title = within(card).getByRole("group", { name: "Title" });
    expect(title).toHaveTextContent("Before: Add search");
    expect(title).toHaveTextContent("After: Add contacts search");
    expect(within(title).getByText("contacts", { exact: false, selector: "ins" })).toBeTruthy();
    const description = within(card).getByRole("group", { name: "Description" });
    expect(
      within(description).getByText("contacts by name", { exact: false, selector: "ins" }),
    ).toBeTruthy();

    const criteria = within(card).getByRole("list", { name: "Acceptance criteria" });
    const items = within(criteria)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(items).toEqual([
      "Unchanged: Shows a hint",
      "Removed: Old rule",
      expect.stringMatching(/^Changed: Lists matches.*Lists matches newest first$/),
      "Added: Empty search returns everything",
    ]);
    expect(within(card).getByText("The card has no criteria an agent could test.")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Accept proposed edit" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Reject proposed edit" })).toBeEnabled();
  });

  it("shows decided and superseded proposals as resolved, without buttons", async () => {
    history = [
      msg("01", "assistant", "First idea.", {
        proposal: proposal({ id: OLDER_PROPOSAL_ID }),
      }),
      msg("02", "assistant", "Second idea.", { proposal: proposal() }),
      msg("03", "assistant", "Third idea.", {
        proposal: proposal({
          id: "00000000-0000-4000-8000-0000000000d2",
          status: "rejected",
          decided_at: "2026-11-26T01:00:00Z",
        }),
      }),
    ];
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const cards = await within(region).findAllByRole("group", { name: "Proposed edit" });
    expect(cards).toHaveLength(3);
    const [older, latest, rejected] = cards as [HTMLElement, HTMLElement, HTMLElement];
    expect(within(older).getByText(/Superseded/)).toBeTruthy();
    expect(within(older).queryByRole("button")).toBeNull();
    expect(within(rejected).getByText(/Rejected/)).toBeTruthy();
    expect(within(rejected).queryByRole("button")).toBeNull();
    // The newest pending proposal stays actionable even though a rejected one came after it.
    expect(within(latest).getByRole("button", { name: "Accept proposed edit" })).toBeTruthy();
  });

  it("accepts with the task's version, refetches the task and notes the move to Backlog", async () => {
    history = [msg("02", "assistant", "Here is an edit.", { proposal: proposal() })];
    let accepted: unknown;
    server.use(
      http.post(`${API}/tasks/:id/chat/proposals/:pid/accept`, async ({ request, params }) => {
        expect(params.pid).toBe(PROPOSAL_ID);
        accepted = await request.json();
        current = { ...current, title: "Add contacts search", column: "backlog", version: 5 };
        const decided = proposal({ status: "accepted", decided_at: "2026-11-26T01:00:00Z" });
        history = [msg("02", "assistant", "Here is an edit.", { proposal: decided })];
        return HttpResponse.json({
          proposal: decided,
          task: current,
          returned_to_backlog: true,
          note: "The card went back to Backlog.",
        } satisfies Schemas["TaskChatProposalAccepted"]);
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const user = userEvent.setup();
    await user.click(await within(region).findByRole("button", { name: "Accept proposed edit" }));

    await waitFor(() => expect(accepted).toEqual({ version: 4 }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Add contacts search" }),
    ).toBeInTheDocument();
    const details = screen.getByRole("region", { name: "Details" });
    expect(within(details).getByText("Backlog")).toBeInTheDocument();
    expect(within(region).getByRole("status")).toHaveTextContent(/moved back to Backlog/);
    const card = within(region).getByRole("group", { name: "Proposed edit" });
    expect(await within(card).findByText(/Accepted/)).toBeTruthy();
    expect(within(card).queryByRole("button")).toBeNull();
  });

  it("explains a stale version, reloads the task and lets the user retry", async () => {
    history = [msg("02", "assistant", "Here is an edit.", { proposal: proposal() })];
    const sent: unknown[] = [];
    server.use(
      http.post(`${API}/tasks/:id/chat/proposals/:pid/accept`, async ({ request }) => {
        const body = (await request.json()) as Schemas["TaskChatProposalAccept"];
        sent.push(body);
        if (body.version !== 5) {
          current = { ...current, version: 5 };
          return problemResponse(409, "version-conflict", { current_version: 5 });
        }
        current = { ...current, title: "Add contacts search", version: 6 };
        return HttpResponse.json({
          proposal: proposal({ status: "accepted" }),
          task: current,
          returned_to_backlog: false,
        });
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const user = userEvent.setup();
    const accept = await within(region).findByRole("button", { name: "Accept proposed edit" });
    await user.click(accept);
    expect(await within(region).findByText(/changed since you opened it/)).toBeInTheDocument();

    await user.click(within(region).getByRole("button", { name: "Accept proposed edit" }));
    await waitFor(() => expect(sent).toEqual([{ version: 4 }, { version: 5 }]));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Add contacts search" }),
    ).toBeInTheDocument();
  });

  it("explains a 409 while an agent run is active", async () => {
    history = [msg("02", "assistant", "Here is an edit.", { proposal: proposal() })];
    server.use(
      http.post(`${API}/tasks/:id/chat/proposals/:pid/accept`, () =>
        problemResponse(409, "version-conflict", {
          detail: "An agent run is active on this card. Wait for it to finish or stop it.",
        }),
      ),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    await userEvent
      .setup()
      .click(await within(region).findByRole("button", { name: "Accept proposed edit" }));
    const alert = await within(region).findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't accept the edit yet");
    expect(alert).toHaveTextContent("Wait for it to finish or stop it.");
    expect(within(region).getByRole("button", { name: "Accept proposed edit" })).toBeEnabled();
  });

  it("rejects a proposal and leaves the card unchanged", async () => {
    history = [msg("02", "assistant", "Here is an edit.", { proposal: proposal() })];
    let rejected = false;
    server.use(
      http.post(`${API}/tasks/:id/chat/proposals/:pid/reject`, () => {
        rejected = true;
        const decided = proposal({ status: "rejected", decided_at: "2026-11-26T01:00:00Z" });
        history = [msg("02", "assistant", "Here is an edit.", { proposal: decided })];
        return HttpResponse.json(decided);
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    await userEvent
      .setup()
      .click(await within(region).findByRole("button", { name: "Reject proposed edit" }));
    await waitFor(() => expect(rejected).toBe(true));
    const card = within(region).getByRole("group", { name: "Proposed edit" });
    expect(await within(card).findByText(/Rejected/)).toBeTruthy();
    expect(within(card).queryByRole("button")).toBeNull();
    expect(within(region).getByRole("status")).toHaveTextContent(/card is unchanged/);
    expect(screen.getByRole("heading", { level: 1, name: "Add search" })).toBeInTheDocument();
  });

  it("clears the conversation only after confirming", async () => {
    history = [msg("01", "user", "Hi")];
    let cleared = 0;
    server.use(
      http.delete(`${API}/tasks/:id/chat`, () => {
        cleared += 1;
        history = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const user = userEvent.setup();
    await user.click(await within(region).findByRole("button", { name: "Clear chat" }));
    const confirm = within(region).getByRole("alertdialog", { name: "Clear this conversation?" });
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(cleared).toBe(0);
    expect(within(region).getByText("Hi")).toBeInTheDocument();

    await user.click(within(region).getByRole("button", { name: "Clear chat" }));
    await user.click(within(region).getByRole("button", { name: "Yes, clear" }));
    expect(await within(region).findByText("No messages yet.")).toBeInTheDocument();
    expect(cleared).toBe(1);
  });

  it("stops a streaming reply on request", async () => {
    let aborted = false;
    server.use(
      http.post(`${API}/tasks/:id/chat`, ({ request }) => {
        request.signal.addEventListener("abort", () => {
          aborted = true;
        });
        return new HttpResponse(
          gatedStream(
            [sseFrame("segment", "s0", { segment_id: "a", seq: 0, part: "chat", text: "Partial" })],
            new Promise(() => {}),
            [],
          ),
          { headers: SSE_HEADERS },
        );
      }),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const user = userEvent.setup();
    await user.type(await within(region).findByRole("textbox", { name: "Message" }), "Hello");
    await user.click(within(region).getByRole("button", { name: "Send" }));
    expect(await within(region).findByText(/Partial/)).toBeInTheDocument();
    await user.click(within(region).getByRole("button", { name: "Stop reply" }));

    await waitFor(() =>
      expect(within(region).getByRole("status")).toHaveTextContent("Reply stopped."),
    );
    expect(within(region).queryByRole("button", { name: "Stop reply" })).toBeNull();
    await waitFor(() => expect(aborted).toBe(true));
    await user.type(within(region).getByRole("textbox", { name: "Message" }), "Again");
    expect(within(region).getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("shows a refused message with the problem details", async () => {
    server.use(
      http.post(`${API}/tasks/:id/chat`, () =>
        problemResponse(422, "guard-blocked", {
          title: "Message blocked",
          detail: "This message can't be sent.",
        }),
      ),
    );
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
    const region = await chatRegion();
    const user = userEvent.setup();
    await user.type(await within(region).findByRole("textbox", { name: "Message" }), "Hi{Enter}");
    const alert = await within(region).findByRole("alert");
    expect(alert).toHaveTextContent("Message blocked");
    expect(alert).toHaveTextContent("Reference: corr-test-0001");
  });

  it("is shown to any allowlisted account, either role", async () => {
    renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />, { email: TESTER });
    expect(await chatRegion()).toBeInTheDocument();
  });

  it("is hidden from an account that can't edit the task", async () => {
    // An account the API doesn't recognise as allowlisted: its role stays unknown.
    let meDone = false;
    server.use(http.get(`${API}/me`, () => problemResponse(403, "forbidden")));
    server.events.on("response:mocked", ({ request }) => {
      if (new URL(request.url).pathname.endsWith("/me")) meDone = true;
    });
    try {
      renderWithApi(<TaskDetailPage taskId={TASK_ID} apiBase={API} />);
      await screen.findByRole("heading", { level: 1, name: "Add search" });
      await waitFor(() => expect(meDone).toBe(true));
      expect(screen.queryByRole("region", { name: "Task chat" })).toBeNull();
      expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    } finally {
      server.events.removeAllListeners("response:mocked");
    }
  });
});
