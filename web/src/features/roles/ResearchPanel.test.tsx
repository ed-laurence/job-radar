import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { API, gatedStream, renderWithApi, SSE_HEADERS, sseFrame } from "@/lib/testing";
import { ResearchPanel } from "./ResearchPanel";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const RUN = "00000000-0000-4000-8000-0000000000f1";
const target = (id: string, name: string) => ({
  id: `00000000-0000-4000-8000-0000000000${id}`,
  name,
  lane: "A",
  status: "approved",
  origin: "manual",
  version: 1,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
});
const step = (seq: number, kind: string, summary: string, tool?: string) => ({
  id: `00000000-0000-4000-8000-0000000002${String(seq).padStart(2, "0")}`,
  run_id: RUN,
  seq,
  kind,
  summary,
  at: "2026-11-25T01:00:00Z",
  ...(tool ? { tool } : {}),
});

describe("ResearchPanel", () => {
  it("starts a run on the matching target and streams its steps live", async () => {
    let release = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let startedOn: string | undefined;
    let body: unknown;
    server.use(
      http.get(`${API}/targets`, () =>
        HttpResponse.json({ items: [target("d1", "Globex"), target("d2", "Acme Pty Ltd")] }),
      ),
      http.post(`${API}/targets/:targetId/research-runs`, async ({ params, request }) => {
        startedOn = String(params.targetId);
        body = await request.json();
        return HttpResponse.json(
          { id: RUN, target_id: startedOn, status: "queued", created_at: "2026-11-25T01:00:00Z" },
          { status: 202 },
        );
      }),
      http.get(
        `${API}/research-runs/:runId/steps`,
        () =>
          new HttpResponse(
            gatedStream(
              [sseFrame("step", "0", step(0, "tool_call", "Searching recent news", "web_search"))],
              gate,
              [
                sseFrame("step", "1", step(1, "tool_result", "Found 3 articles", "web_search")),
                sseFrame("done", "d", { status: "succeeded" }),
              ],
            ),
            { headers: SSE_HEADERS },
          ),
      ),
      http.get(`${API}/research-runs/:runId`, () =>
        HttpResponse.json({
          id: RUN,
          target_id: "x",
          status: "succeeded",
          summary: "Acme is **hiring**.",
          created_at: "2026-11-25T01:00:00Z",
        }),
      ),
    );
    renderWithApi(<ResearchPanel company="Acme Pty Ltd" />);
    const select = await screen.findByRole("combobox", { name: "Employer" });
    expect(select).toHaveValue("00000000-0000-4000-8000-0000000000d2");
    await userEvent.click(screen.getByRole("checkbox", { name: "Hiring signals" }));
    await userEvent.click(screen.getByRole("button", { name: "Start research" }));

    const steps = await screen.findByRole("list", { name: "Research steps" });
    expect(await within(steps).findByText("Searching recent news")).toBeInTheDocument();
    expect(within(steps).getByText(/Tool call · web_search/)).toBeInTheDocument();
    await act(async () => release());
    expect(await within(steps).findByText("Found 3 articles")).toBeInTheDocument();
    expect(await screen.findByText("hiring")).toBeInTheDocument();
    expect(startedOn).toBe("00000000-0000-4000-8000-0000000000d2");
    expect(body).toEqual({ focus: ["overview", "hiring_signals"] });
  });

  it("explains that research needs an employer", async () => {
    server.use(http.get(`${API}/targets`, () => HttpResponse.json({ items: [] })));
    renderWithApi(<ResearchPanel company="Acme Pty Ltd" />);
    expect(await screen.findByText(/No employers yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start research" })).not.toBeInTheDocument();
  });
});
