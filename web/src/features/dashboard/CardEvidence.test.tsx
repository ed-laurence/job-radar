import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problemResponse, renderWithApi } from "@/lib/testing";
import { CardEvidence } from "./CardEvidence";

const ROLE_ID = "00000000-0000-4000-8000-0000000000a1";

const ROLE: Schemas["Role"] = {
  id: ROLE_ID,
  source: "feed",
  title: "Senior Data Engineer",
  stage: "scored",
  status: "new",
  found_at: "2026-11-26T10:00:00Z",
  rank: "a0",
  version: 1,
  created_at: "2026-11-26T10:00:00Z",
  updated_at: "2026-11-26T10:00:00Z",
  evidence: {
    must_haves_total: 2,
    shown: 1,
    from_records: 0,
    verdict: "consider",
    summary: "",
    items: [
      { requirement: "Spark", match: "shown", source: "cv" },
      { requirement: "Kafka", match: "missing", source: "none" },
    ],
  },
};

const FULL: Schemas["RoleEvidenceTest"] = {
  role_id: ROLE_ID,
  verdict: "consider",
  verdict_text: "Don't cold-apply: go through a recruiter.",
  shown: 1,
  total: 2,
  from_records: 0,
  items: [
    {
      requirement: "Spark",
      match: "shown",
      quote: "Built Spark pipelines for a payments client.",
      source: "cv_technical",
      why: "Named on the CV.",
    },
    { requirement: "Kafka", match: "missing", source: "none", why: "Not mentioned." },
  ],
  team_check: { status: "over", text: "more than twice what you've led." },
  checked_at: "2026-11-25T20:02:00Z",
  model: "replay",
};

let gets = 0;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  gets = 0;
  server.use(
    http.get(`${API}/settings`, () => problemResponse(404, "not-found")),
    http.get(`${API}/roles/:roleId/evidence`, () => {
      gets += 1;
      return HttpResponse.json(FULL);
    }),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("CardEvidence", () => {
  it("shows the summary line collapsed and loads the full test only when opened", async () => {
    renderWithApi(<CardEvidence role={ROLE} />);
    const summary = screen.getByText(/you show 1 of 2 must-haves/);
    expect(summary).toHaveTextContent("Consider · you show 1 of 2 must-haves");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    await new Promise((r) => setTimeout(r, 50));
    expect(gets).toBe(0);

    await userEvent.click(summary);
    const quote = await screen.findByText(/Built Spark pipelines/);
    expect(quote.tagName).toBe("BLOCKQUOTE");
    expect(gets).toBe(1);
    const list = screen.getByRole("list", { name: "Must-haves" });
    expect(within(list).getByText("technical CV")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: "I've done this: Kafka" })).toBeEnabled();
    expect(screen.getByText("Team: more than twice what you've led.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
  });

  it("offers the first run on a card without evidence", async () => {
    let posts = 0;
    server.use(
      http.post(`${API}/roles/:roleId/evidence`, () => {
        posts += 1;
        return HttpResponse.json(FULL);
      }),
    );
    renderWithApi(<CardEvidence role={{ ...ROLE, evidence: undefined }} />);
    await userEvent.click(screen.getByRole("button", { name: "Run the evidence test" }));
    await waitFor(() => expect(posts).toBe(1));
    const strip = await screen.findByRole("group");
    expect(strip.querySelector("summary")).toHaveTextContent(
      "Consider · you show 1 of 2 must-haves",
    );
  });
});
