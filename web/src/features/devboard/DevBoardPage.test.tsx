import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, meHandler, problemResponse, renderWithApi, TESTER } from "@/lib/testing";
import { DevBoardPage } from "./DevBoardPage";
import { layoutBoard, task } from "./testing";

const track = vi.fn();
vi.mock("@/lib/AnalyticsProvider", () => ({ useAnalytics: () => ({ track }) }));

const ID = (n: number) => `00000000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`;

let tasks: Schemas["Task"][];
let moves: { id: string; body: Schemas["TaskMove"] }[];
let listCalls: number;
let moveResponse: (id: string, body: Schemas["TaskMove"]) => Response | Promise<Response>;

const server = setupServer(
  meHandler,
  http.get(`${API}/tasks`, () => {
    listCalls++;
    return HttpResponse.json({ items: tasks });
  }),
  http.post(`${API}/tasks/:taskId/move`, async ({ params, request }) => {
    const body = (await request.json()) as Schemas["TaskMove"];
    const id = String(params.taskId);
    moves.push({ id, body });
    return moveResponse(id, body);
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  track.mockReset();
  listCalls = 0;
  moves = [];
  tasks = [
    task({ id: ID(1), title: "Add empty state", column: "backlog", rank: "a" }),
    task({ id: ID(2), title: "Fix footer link", column: "backlog", rank: "b" }),
    task({ id: ID(3), title: "Polish chart", column: "triaged", rank: "m", version: 3 }),
    task({
      id: ID(4),
      title: "Rotate the deploy key",
      column: "out_of_scope",
      rank: "a",
      out_of_scope_reason: "Needs secrets and infra.",
    }),
  ];
  moveResponse = (id, body) => {
    const current = tasks.find((t) => t.id === id);
    return HttpResponse.json({
      ...current,
      column: body.to_column,
      rank: body.rank,
      version: (current?.version ?? 1) + 1,
    });
  };
});

function column(label: string) {
  return screen.getByRole("region", { name: label });
}

/** Picks up a card with the keyboard, presses arrows, and drops it (dnd-kit KeyboardSensor). */
async function keyboardDrag(title: string, keys: string[]) {
  const user = userEvent.setup();
  const handle = screen.getByRole("button", { name: `Move “${title}”` });
  handle.focus();
  await user.keyboard("[Space]");
  for (const key of keys) await user.keyboard(key);
  await user.keyboard("[Space]");
}

describe("DevBoardPage", () => {
  it("renders every spec column with its label, in order", async () => {
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Add empty state");
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "Backlog",
      "Triaged",
      "Out of scope",
      "Plan review",
      "Ready",
      "Agent dev",
      "Agent review",
      "Changes requested",
      "Human review",
      "Blocked",
      "Done",
    ]);
    expect(within(column("Backlog")).getAllByRole("listitem", { name: /./ })).toHaveLength(2);
    expect(
      within(column("Backlog")).getByRole("link", { name: "Add empty state" }),
    ).toHaveAttribute("href", `/dev-board/${ID(1)}`);
  });

  it("shows out-of-scope cards as locked with the reason", async () => {
    renderWithApi(<DevBoardPage />);
    const card = (await screen.findByText("Rotate the deploy key")).closest("li");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("Locked")).toBeInTheDocument();
    expect(within(card as HTMLElement).getByText(/Needs secrets and infra/)).toBeInTheDocument();
  });

  it("drags with the keyboard and announces it (CODE-23)", async () => {
    layoutBoard();
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Polish chart");
    const handle = screen.getByRole("button", { name: "Move “Polish chart”" });
    expect(handle).toHaveAttribute("aria-roledescription", "sortable");
    expect(handle).toHaveAccessibleDescription(/press Space or Enter to pick it up/i);

    // Triaged → Out of scope → Plan review.
    await keyboardDrag("Polish chart", ["[ArrowRight]", "[ArrowRight]"]);

    await waitFor(() => expect(moves).toHaveLength(1));
    // dnd-kit's live region carries our announcement.
    expect(screen.getByText("Dropped “Polish chart” in Plan review. Saving…")).toBeInTheDocument();
    expect(moves[0]).toMatchObject({ id: ID(3), body: { to_column: "plan_review", version: 3 } });
    expect(moves[0]?.body.rank).toMatch(/^[0-9A-Za-z]{1,64}$/);
    await waitFor(() =>
      expect(within(column("Plan review")).getByText("Polish chart")).toBeInTheDocument(),
    );
    expect(await screen.findByText("Moved “Polish chart” to Plan review.")).toBeInTheDocument();
    expect(track).toHaveBeenCalledWith("card_moved");
    expect(track).toHaveBeenCalledWith("agent_run_started");
  });

  it("moves a card to the previous column with ArrowLeft, not within its own column", async () => {
    layoutBoard();
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Polish chart");

    await keyboardDrag("Polish chart", ["[ArrowLeft]"]);

    await waitFor(() => expect(moves).toHaveLength(1));
    expect(screen.getByText("Dropped “Polish chart” in Backlog. Saving…")).toBeInTheDocument();
    // Appended below the last Backlog card ("b").
    expect(moves[0]).toMatchObject({ id: ID(3), body: { to_column: "backlog" } });
    expect((moves[0]?.body.rank ?? "") > "b").toBe(true);
    expect(await screen.findByText("Moved “Polish chart” to Backlog.")).toBeInTheDocument();
  });

  it("moves a card within its column with ArrowUp", async () => {
    layoutBoard();
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Fix footer link");

    await keyboardDrag("Fix footer link", ["[ArrowUp]"]);

    await waitFor(() => expect(moves).toHaveLength(1));
    expect(moves[0]).toMatchObject({ id: ID(2), body: { to_column: "backlog" } });
    expect((moves[0]?.body.rank ?? "a") < "a").toBe(true);
  });

  it("moves optimistically, then rolls back and explains a 422 illegal move", async () => {
    layoutBoard();
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    moveResponse = async () => {
      await gate;
      return problemResponse(422, "illegal-transition", {
        detail: "Only triage moves cards out of Backlog.",
        allowed_columns: ["blocked"],
      });
    };
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Fix footer link");

    await keyboardDrag("Fix footer link", ["[ArrowRight]"]);

    // Optimistic: the card is in Triaged before the server answers.
    await waitFor(() =>
      expect(within(column("Triaged")).getByText("Fix footer link")).toBeInTheDocument(),
    );
    await act(async () => release());

    const alert = await screen.findByText(/Can't move “Fix footer link” from Backlog to Triaged/);
    expect(alert).toHaveTextContent("Only triage moves cards out of Backlog.");
    expect(alert).toHaveTextContent("Allowed: Blocked.");
    expect(within(column("Backlog")).getByText("Fix footer link")).toBeInTheDocument();
    expect(within(column("Triaged")).queryByText("Fix footer link")).toBeNull();
    expect(track).not.toHaveBeenCalled();
  });

  it("rolls back and refreshes the board on a 409", async () => {
    layoutBoard();
    moveResponse = () => {
      // Someone else moved it meanwhile; the refetch shows their state.
      tasks = tasks.map((t) => (t.id === ID(3) ? { ...t, column: "ready", version: 4 } : t));
      return problemResponse(409, "version-conflict", { current_version: 4 });
    };
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Polish chart");
    const before = listCalls;

    await keyboardDrag("Polish chart", ["[ArrowRight]", "[ArrowRight]"]);

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument();
    await waitFor(() => expect(listCalls).toBeGreaterThan(before));
    await waitFor(() =>
      expect(within(column("Ready")).getByText("Polish chart")).toBeInTheDocument(),
    );
  });

  it("explains a 403 maintainer-only move", async () => {
    layoutBoard();
    moveResponse = () => problemResponse(403, "forbidden");
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Polish chart");
    await keyboardDrag("Polish chart", ["[ArrowRight]", "[ArrowRight]"]);
    expect(
      await screen.findByText("Only a maintainer can move a card to Plan review."),
    ).toBeInTheDocument();
    expect(within(column("Triaged")).getByText("Polish chart")).toBeInTheDocument();
  });

  it("shows maintainer-only columns as unavailable to a user before any request", async () => {
    layoutBoard();
    renderWithApi(<DevBoardPage />, { email: TESTER });
    await screen.findByText("Polish chart");
    await waitFor(() => expect(within(column("Ready")).getByText("Maintainer only")).toBeVisible());
    expect(within(column("Backlog")).queryByText("Maintainer only")).toBeNull();

    const user = userEvent.setup();
    screen.getByRole("button", { name: "Move “Polish chart”" }).focus();
    await user.keyboard("[Space]");
    expect(within(column("Plan review")).getByText("Can’t drop here")).toBeInTheDocument();
    expect(within(column("Backlog")).queryByText("Can’t drop here")).toBeNull();
    await user.keyboard("[Escape]");
    expect(moves).toHaveLength(0);
  });

  it("asks for a reason before moving a card to Blocked", async () => {
    layoutBoard();
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Rotate the deploy key");
    // Out of scope → Blocked: the locked columns in between are skipped by the keyboard.
    await keyboardDrag("Rotate the deploy key", ["[ArrowRight]"]);
    const dialog = await screen.findByRole("alertdialog", { name: "Block this card" });
    const user = userEvent.setup();
    await user.type(within(dialog).getByLabelText("Reason"), "Waiting on a design");
    await user.click(within(dialog).getByRole("button", { name: "Move to Blocked" }));
    await waitFor(() => expect(moves).toHaveLength(1));
    expect(moves[0]?.body).toMatchObject({ to_column: "blocked", reason: "Waiting on a design" });
  });

  it("creates a task from the drawer and tells the author about redaction", async () => {
    const created: Schemas["TaskCreate"][] = [];
    server.use(
      http.post(`${API}/tasks`, async ({ request }) => {
        const body = (await request.json()) as Schemas["TaskCreate"];
        created.push(body);
        const t = task({
          id: ID(9),
          title: body.title,
          column: "backlog",
          rank: "c",
          pii_redacted: true,
        });
        tasks = [...tasks, t];
        return HttpResponse.json(t, { status: 201 });
      }),
    );
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Add empty state");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "New task" }));
    const drawer = screen.getByRole("dialog", { name: "New task" });
    expect(within(drawer).getByLabelText("Title")).toHaveFocus();
    await user.type(within(drawer).getByLabelText("Title"), "Show empty contacts hint");
    await user.type(within(drawer).getByLabelText("Description"), "When the list is empty.");
    await user.type(within(drawer).getByLabelText(/Labels/), "web, ux");
    await user.click(within(drawer).getByRole("button", { name: "Create task" }));

    expect(created).toEqual([
      { title: "Show empty contacts hint", body: "When the list is empty.", labels: ["web", "ux"] },
    ]);
    expect(await screen.findByText(/Personal information was removed/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() =>
      expect(within(column("Backlog")).getByText("Show empty contacts hint")).toBeInTheDocument(),
    );
  });

  it("closes the drawer with Escape and shows a create error", async () => {
    server.use(
      http.post(`${API}/tasks`, () =>
        problemResponse(422, "validation-failed", {
          errors: [{ pointer: "/title", message: "Title is too vague." }],
        }),
      ),
    );
    renderWithApi(<DevBoardPage />);
    await screen.findByText("Add empty state");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "New task" }));
    await user.type(screen.getByLabelText("Title"), "x");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    expect(await screen.findByText(/Title is too vague\./)).toBeInTheDocument();
    await user.keyboard("[Escape]");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "New task" })).toHaveFocus();
  });
});
