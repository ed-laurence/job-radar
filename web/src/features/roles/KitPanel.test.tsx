import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { AnalyticsProvider } from "@/lib/AnalyticsProvider";
import type { WebEvent } from "@/lib/analytics";
import { API, gatedStream, problem, renderWithApi, SSE_HEADERS, sseFrame } from "@/lib/testing";
import { KitPanel } from "./KitPanel";
import { makeRole } from "./testing";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/roles/00000000-0000-4000-8000-0000000000a1",
}));
vi.mock("web-vitals", () => ({
  onLCP: () => {},
  onINP: () => {},
  onCLS: () => {},
  onFCP: () => {},
  onTTFB: () => {},
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const seg = (id: string, seq: number, text: string, part = "cover_note") =>
  sseFrame("segment", String(seq), { segment_id: id, seq, part, text });

describe("KitPanel", () => {
  it("renders segments progressively, redacts retracted ones and finishes", async () => {
    let release = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let body: unknown;
    server.use(
      http.post(`${API}/roles/:roleId/kit`, async ({ request }) => {
        body = await request.json();
        return new HttpResponse(
          gatedStream(
            [seg("s1", 0, "Dear hiring team, "), seg("s2", 1, "call me on 0400 000 000. ")],
            gate,
            [
              sseFrame("retract", "r1", {
                segment_id: "s2",
                replacement: "anything the server says",
                reason: "pii",
              }),
              seg("s3", 2, "**Thanks**", "outreach"),
              sseFrame("done", "d", { result_id: "00000000-0000-4000-8000-0000000000b1" }),
            ],
          ),
          { headers: SSE_HEADERS },
        );
      }),
      http.get(`${API}/roles/:roleId`, () => HttpResponse.json(makeRole())),
    );
    const sent: WebEvent[] = [];
    const view = renderWithApi(
      <AnalyticsProvider
        send={async (events) => {
          sent.push(...events);
        }}
      >
        <KitPanel role={makeRole()} />
      </AnalyticsProvider>,
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "CV" }));
    await userEvent.click(screen.getByRole("button", { name: "Generate kit" }));

    const output = await screen.findByRole("region", { name: "Generated kit" });
    expect(await within(output).findByText(/Dear hiring team/)).toBeInTheDocument();
    expect(within(output).getByText(/0400 000 000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop generating" })).toBeInTheDocument();

    await act(async () => release());
    expect(await within(output).findByText("[redacted]")).toBeInTheDocument();
    expect(within(output).queryByText(/0400 000 000/)).not.toBeInTheDocument();
    expect(within(output).queryByText(/anything the server says/)).not.toBeInTheDocument();
    expect(within(output).getByText("Thanks").tagName).toBe("STRONG");
    expect(await screen.findByText("Kit generated.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop generating" })).not.toBeInTheDocument();
    expect(body).toEqual({ parts: ["cover_note", "outreach", "answers"] });

    view.unmount();
    await waitFor(() =>
      expect(sent).toContainEqual(
        expect.objectContaining({ type: "product", name: "kit_generated" }),
      ),
    );
    expect(sent.every((e) => e.route === "/roles/[roleId]")).toBe(true);
  });

  it("stops a generation on request", async () => {
    let aborted = false;
    server.use(
      http.post(`${API}/roles/:roleId/kit`, ({ request }) => {
        request.signal.addEventListener("abort", () => {
          aborted = true;
        });
        return new HttpResponse(
          gatedStream([seg("s1", 0, "First line. ")], new Promise(() => {}), []),
          {
            headers: SSE_HEADERS,
          },
        );
      }),
    );
    renderWithApi(<KitPanel role={makeRole()} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate kit" }));
    expect(await screen.findByText(/First line/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop generating" }));

    expect(await screen.findByText("Stopped. Partial output is not saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate kit" })).toBeEnabled();
    await waitFor(() => expect(aborted).toBe(true));
  });

  it("shows a stream error event", async () => {
    server.use(
      http.post(
        `${API}/roles/:roleId/kit`,
        () =>
          new HttpResponse(
            gatedStream(
              [
                sseFrame(
                  "error",
                  "e",
                  problem(429, "budget-exceeded", { title: "Budget exceeded" }),
                ),
              ],
              Promise.resolve(),
              [],
            ),
            { headers: SSE_HEADERS },
          ),
      ),
    );
    renderWithApi(<KitPanel role={makeRole()} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate kit" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Budget exceeded");
    expect(alert).toHaveTextContent("corr-test-0001");
  });

  it("explains a 409 when a kit is already being generated", async () => {
    server.use(
      http.post(`${API}/roles/:roleId/kit`, () =>
        HttpResponse.json(problem(409, "version-conflict", { title: "Conflict" }), {
          status: 409,
        }),
      ),
    );
    renderWithApi(<KitPanel role={makeRole()} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate kit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already being generated/i);
  });

  it("shows the saved kit when not generating", () => {
    renderWithApi(
      <KitPanel
        role={makeRole({
          kit: {
            version: 2,
            generated_at: "2026-11-22T00:00:00Z",
            cover_note: "## Cover\n\nHello",
            answers: [{ q: "Why us?", a: "Because." }],
          },
        })}
      />,
    );
    const saved = screen.getByRole("region", { name: "Saved kit" });
    expect(within(saved).getByRole("heading", { name: "Cover" })).toBeInTheDocument();
    expect(within(saved).getByText("Why us?")).toBeInTheDocument();
  });
});
