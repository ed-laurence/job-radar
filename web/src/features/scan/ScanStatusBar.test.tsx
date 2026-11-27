import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problemResponse, renderWithApi, TESTER } from "@/lib/testing";
import { ScanStatusBar } from "./ScanStatusBar";

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

// Mocks typed by the spec's schemas, so they break when the contract changes.
function run(over: Partial<Schemas["ScanRun"]> = {}): Schemas["ScanRun"] {
  return {
    id: "00000000-0000-4000-8000-0000000000e1",
    started_at: minutesAgo(4),
    finished_at: minutesAgo(3),
    trigger: "schedule",
    source: "seed",
    status: "ok",
    fetched: 5,
    new: 5,
    duplicates: 0,
    scored: 5,
    failed: 0,
    pool_remaining: 40,
    ...over,
  };
}

let status: Schemas["ScanStatus"];
const posts: number[] = [];
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  posts.length = 0;
  status = {
    last_run: run(),
    next_run_at: minutesAgo(-12),
    interval_minutes: 15,
    pool_remaining: 40,
    running: false,
  };
  server.use(http.get(`${API}/scan/status`, () => HttpResponse.json(status)));
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("ScanStatusBar", () => {
  it("shows the last and next scan with a green dot after a good scan", async () => {
    renderWithApi(<ScanStatusBar maintainer />);
    expect(await screen.findByText("Last scan 3m ago · next in 12 min")).toBeInTheDocument();
    expect(screen.getByText("Last scan OK")).toHaveClass("visually-hidden");
    expect(document.querySelector(".dot")).toHaveClass("ok");
  });

  it("turns the dot amber when the last scan failed", async () => {
    status = { ...status, last_run: run({ status: "failed", new: 0 }) };
    renderWithApi(<ScanStatusBar maintainer />);
    expect(await screen.findByText("Last scan failed or no scan has run")).toBeInTheDocument();
    expect(document.querySelector(".dot")).toHaveClass("warn");
  });

  it("turns the dot amber when no scan has run", async () => {
    status = { ...status, last_run: null, next_run_at: null };
    renderWithApi(<ScanStatusBar maintainer />);
    expect(await screen.findByText("No scans yet")).toBeInTheDocument();
    expect(document.querySelector(".dot")).toHaveClass("warn");
  });

  it("Scan now is busy while it runs, then reports the new roles in a toast", async () => {
    let finish: (() => void) | undefined;
    server.use(
      http.post(`${API}/scan`, async () => {
        posts.push(1);
        await new Promise<void>((r) => {
          finish = r;
        });
        return HttpResponse.json(run({ trigger: "manual", new: 5 }));
      }),
    );
    const user = userEvent.setup();
    renderWithApi(<ScanStatusBar maintainer />);
    const button = await screen.findByRole("button", { name: "Scan now" });
    await user.click(button);
    const busy = await screen.findByRole("button", { name: "Scanning…" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    await waitFor(() => expect(finish).toBeDefined());
    finish?.();
    // The toast region is always mounted (so it announces); wait for its text.
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("5 new roles"));
    expect(await screen.findByRole("button", { name: "Scan now" })).toBeEnabled();
    expect(posts).toEqual([1]);
  });

  it("says No new roles when the pool had nothing", async () => {
    server.use(
      http.post(`${API}/scan`, () =>
        HttpResponse.json(run({ trigger: "manual", status: "no_new_roles", new: 0 })),
      ),
    );
    const user = userEvent.setup();
    renderWithApi(<ScanStatusBar maintainer />);
    await user.click(await screen.findByRole("button", { name: "Scan now" }));
    // The toast region is always mounted (so it announces); wait for its text.
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("No new roles"));
  });

  it("is disabled while a scheduled scan is running", async () => {
    status = { ...status, running: true };
    renderWithApi(<ScanStatusBar maintainer />);
    expect(await screen.findByText("Scanning now…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scanning…" })).toBeDisabled();
  });

  it("explains a conflict with a running scan", async () => {
    server.use(http.post(`${API}/scan`, () => problemResponse(409, "scan-running")));
    const user = userEvent.setup();
    renderWithApi(<ScanStatusBar maintainer />);
    await user.click(await screen.findByRole("button", { name: "Scan now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A scan is already running.");
  });

  it("shows nothing to users and never asks for the maintainer-only status", async () => {
    // onUnhandledRequest: "error" would fail a request; the handler is removed to prove none is made.
    server.resetHandlers();
    const { container } = renderWithApi(<ScanStatusBar maintainer={false} />, { email: TESTER });
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });
});
