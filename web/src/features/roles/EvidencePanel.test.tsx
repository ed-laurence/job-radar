import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problemResponse, renderWithApi } from "@/lib/testing";
import { EvidencePanel } from "./EvidencePanel";
import { makeRole, ROLE_ID } from "./testing";

type EvidenceTest = Schemas["RoleEvidenceTest"];

const FIRST: EvidenceTest = {
  role_id: ROLE_ID,
  verdict: "consider",
  verdict_text:
    "Don't cold-apply: go through a recruiter, a referral or a note to the hiring manager.",
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
  team_check: { status: "not_stated", text: "the ad doesn't say." },
  checked_at: "2026-11-25T20:02:00Z",
  model: "replay",
};

const AFTER: EvidenceTest = {
  ...FIRST,
  verdict: "apply",
  verdict_text: "Passes the evidence test and scores 70+: apply.",
  shown: 2,
  from_records: 1,
  items: [
    FIRST.items[0] as Schemas["EvidenceTestItem"],
    {
      requirement: "Kafka",
      match: "shown",
      quote: "Ran the Kafka cluster for the event platform.",
      source: "records",
      record_id: "00000000-0000-4000-8000-0000000000e1",
      record_title: "Kafka at Example Co",
      why: "From your records.",
    },
  ],
  checked_at: "2026-11-26T20:02:00Z",
};

let stored: EvidenceTest | null = FIRST;
let posts = 0;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  stored = FIRST;
  posts = 0;
  server.use(
    http.get(`${API}/settings`, () => problemResponse(404, "not-found")),
    http.get(`${API}/experience`, () =>
      HttpResponse.json({
        items: [],
        contexts: ["Platform Architect, Example Co", "Personal project", "Other"],
      }),
    ),
    http.get(`${API}/roles/:roleId/evidence`, () =>
      stored ? HttpResponse.json(stored) : problemResponse(404, "not-found"),
    ),
    http.post(`${API}/roles/:roleId/evidence`, () => {
      posts += 1;
      stored = AFTER;
      return HttpResponse.json(AFTER);
    }),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const role = makeRole({
  evidence: { must_haves_total: 2, shown: 1, from_records: 0, verdict: "consider", summary: "" },
});

async function block() {
  await screen.findByRole("list", { name: "Must-haves" });
  return screen.getByRole("region", { name: "Evidence test" });
}

describe("EvidencePanel", () => {
  it("loads the full evidence test, expanded", async () => {
    renderWithApi(<EvidencePanel role={role} />);
    const region = await block();
    await waitFor(() =>
      expect(within(region).getByText(/Built Spark pipelines/).tagName).toBe("BLOCKQUOTE"),
    );
    expect(region).toHaveTextContent("Consider · you show 1 of 2 must-haves");
    expect(region).toHaveTextContent("Team: the ad doesn't say.");
    expect(region).toHaveTextContent(/Checked \w{3}, \d+ Nov, \d+:\d{2} (am|pm)/);
  });

  it("Run again posts, shows a busy state, then the new result", async () => {
    let release: () => void = () => undefined;
    server.use(
      http.post(`${API}/roles/:roleId/evidence`, async () => {
        posts += 1;
        await new Promise<void>((r) => {
          release = r;
        });
        return HttpResponse.json(AFTER);
      }),
    );
    renderWithApi(<EvidencePanel role={role} />);
    const region = await block();
    await waitFor(() => expect(within(region).getByText(/Built Spark/)).toBeInTheDocument());
    await userEvent.click(within(region).getByRole("button", { name: "Run again" }));
    const busy = await within(region).findByRole("button", { name: "Running…" });
    expect(busy).toBeDisabled();
    release();
    await waitFor(() =>
      expect(region).toHaveTextContent("Apply · you show 2 of 2 must-haves (1 from your records"),
    );
    expect(posts).toBe(1);
    expect(region).toHaveTextContent("Evidence test updated: you show 2 of 2 must-haves.");
  });

  it("explains the AI rate limit and pauses Run again", async () => {
    server.use(
      http.post(`${API}/roles/:roleId/evidence`, () => problemResponse(429, "rate-limited")),
    );
    renderWithApi(<EvidencePanel role={role} />);
    const region = await block();
    await waitFor(() => expect(within(region).getByText(/Built Spark/)).toBeInTheDocument());
    await userEvent.click(within(region).getByRole("button", { name: "Run again" }));
    expect(await within(region).findByRole("alert")).toHaveTextContent("The AI is busy");
    expect(within(region).getByRole("button", { name: "Run again" })).toBeDisabled();
    expect(region).toHaveTextContent("Wait a moment before running it again.");
  });

  it("explains a spent AI budget", async () => {
    server.use(
      http.post(`${API}/roles/:roleId/evidence`, () => problemResponse(429, "budget-exceeded")),
    );
    renderWithApi(<EvidencePanel role={role} />);
    const region = await block();
    await waitFor(() => expect(within(region).getByText(/Built Spark/)).toBeInTheDocument());
    await userEvent.click(within(region).getByRole("button", { name: "Run again" }));
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Today's AI budget is used up",
    );
  });

  it("I've done this saves a record for the requirement and role, then runs the test again", async () => {
    const records: Schemas["ExperienceCreate"][] = [];
    server.use(
      http.post(`${API}/experience`, async ({ request }) => {
        const body = (await request.json()) as Schemas["ExperienceCreate"];
        records.push(body);
        const saved: Schemas["ExperienceRecord"] = {
          id: "00000000-0000-4000-8000-0000000000e1",
          ref: "E1",
          requirement: body.requirement,
          entries: body.entries.map((e, k) => ({ ...e, ref: `E1.${k + 1}` })),
          uses: 0,
          version: 1,
          created_at: "2026-11-26T20:00:00Z",
          updated_at: "2026-11-26T20:00:00Z",
        };
        return HttpResponse.json(saved, { status: 201 });
      }),
    );
    renderWithApi(<EvidencePanel role={role} />);
    const region = await block();
    const done = await within(region).findByRole("button", { name: "I've done this: Kafka" });
    expect(within(region).queryByRole("button", { name: "I've done this: Spark" })).toBeNull();
    await userEvent.click(done);

    const dialog = screen.getByRole("dialog", { name: "I've done this: Kafka" });
    // The "Where" choices come from the experience list (the roles on your CVs).
    const where = within(dialog).getByRole("combobox", { name: "Where" });
    await within(where).findByRole("option", { name: "Platform Architect, Example Co" });
    await userEvent.selectOptions(where, "Platform Architect, Example Co");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "What you did there" }),
      "Ran the Kafka cluster for the event platform.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Save and run again" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(region).toHaveTextContent("your records · not on CV yet"));
    expect(region).toHaveTextContent("Kafka at Example Co");
    expect(records).toEqual([
      {
        requirement: "Kafka",
        entries: [
          {
            context: "Platform Architect, Example Co",
            detail: "Ran the Kafka cluster for the event platform.",
          },
        ],
        role_id: ROLE_ID,
      },
    ]);
    expect(posts).toBe(1);
    expect(region).toHaveTextContent("Saved Kafka to your records.");
  });

  it("keeps the dialog open with the error when the record can't be saved", async () => {
    server.use(http.post(`${API}/experience`, () => problemResponse(422, "validation-failed")));
    renderWithApi(<EvidencePanel role={role} />);
    const region = await block();
    await userEvent.click(
      await within(region).findByRole("button", { name: "I've done this: Kafka" }),
    );
    const dialog = screen.getByRole("dialog", { name: "I've done this: Kafka" });
    const where = within(dialog).getByRole("combobox", { name: "Where" });
    await within(where).findByRole("option", { name: "Other" });
    await userEvent.selectOptions(where, "Other");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "What you did there" }),
      "Did it",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Save and run again" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("validation failed");
    expect(posts).toBe(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers the first run when the role has never been checked", async () => {
    stored = null;
    renderWithApi(<EvidencePanel role={makeRole()} />);
    const run = await screen.findByRole("button", { name: "Run the evidence test" });
    expect(screen.getByRole("region", { name: "Evidence test" })).toHaveTextContent(
      "Evidence test not run yet.",
    );
    await userEvent.click(run);
    expect(await screen.findByRole("list", { name: "Must-haves" })).toBeInTheDocument();
    expect(posts).toBe(1);
  });

  it("shows a load error with a retry", async () => {
    let fail = true;
    server.use(
      http.get(`${API}/roles/:roleId/evidence`, () =>
        fail ? problemResponse(500, "internal") : HttpResponse.json(FIRST),
      ),
    );
    renderWithApi(<EvidencePanel role={role} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("internal");
    fail = false;
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Built Spark pipelines/)).toBeInTheDocument();
  });
});
