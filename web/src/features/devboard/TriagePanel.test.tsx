import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { TriagePanel } from "./TriagePanel";

const TASK_ID = "00000000-0000-4000-8000-0000000000b1";
const ORIGINAL_ID = "00000000-0000-4000-8000-0000000000b2";

function triage(overrides: Partial<Schemas["TriageResult"]> = {}): Schemas["TriageResult"] {
  return {
    task_id: TASK_ID,
    type: "bug",
    size: "S",
    risk: "low",
    areas: ["web"],
    priority: "P1",
    severity: "major",
    impact: "Every user with no contacts sees a blank page.",
    route: "next",
    route_reason: "Small fix, but the current milestone is full.",
    verification: {
      status: "confirmed",
      evidence: "The contacts list renders nothing when the page is empty.",
      code_refs: ["web/src/features/contacts/ContactsList.tsx", "web/tests/e2e/contacts.spec.ts"],
    },
    needs_human_design: false,
    in_scope: true,
    created_at: "2026-11-25T00:00:00Z",
    ...overrides,
  };
}

/** The `dd` that follows the `dt` with this term, inside the Triage region. */
function fact(term: string): HTMLElement {
  const region = screen.getByRole("region", { name: "Triage" });
  const dt = within(region).getByText(term, { selector: "dt" });
  return dt.nextElementSibling as HTMLElement;
}

describe("TriagePanel", () => {
  it("lists the likely paths as monospace paths", () => {
    render(
      <TriagePanel
        triage={triage({ likely_paths: ["web/src/features/contacts/", "web/src/app/page.tsx"] })}
        loading={false}
      />,
    );
    const list = screen.getByRole("list", { name: "Likely paths" });
    const paths = within(list)
      .getAllByRole("listitem")
      .map((item) => item.querySelector("code")?.textContent);
    expect(paths).toEqual(["web/src/features/contacts/", "web/src/app/page.tsx"]);
  });

  it("says when triage named no likely paths", () => {
    render(<TriagePanel triage={triage({ likely_paths: [] })} loading={false} />);
    expect(screen.getByText("No likely paths.")).toBeInTheDocument();
  });

  it("shows priority, severity, impact and the route with its reason", () => {
    render(<TriagePanel triage={triage()} loading={false} />);
    expect(fact("Priority")).toHaveTextContent("P1");
    expect(fact("Severity")).toHaveTextContent("Major");
    expect(fact("Impact")).toHaveTextContent("Every user with no contacts sees a blank page.");
    expect(fact("Route")).toHaveTextContent("Next");
    expect(fact("Route")).toHaveTextContent("Small fix, but the current milestone is full.");
  });

  it("keeps type, size, risk and areas", () => {
    render(
      <TriagePanel triage={triage({ type: "security", areas: ["web", "api"] })} loading={false} />,
    );
    expect(fact("Type")).toHaveTextContent("security");
    expect(fact("Size")).toHaveTextContent("S");
    expect(fact("Risk")).toHaveTextContent("low");
    expect(fact("Areas")).toHaveTextContent("web, api");
  });

  it("shows the verification status, evidence and code refs as paths", () => {
    render(<TriagePanel triage={triage()} loading={false} />);
    const region = screen.getByRole("region", { name: "Triage" });
    expect(within(region).getByRole("heading", { name: "Verification" })).toBeVisible();
    expect(fact("Status")).toHaveTextContent("Confirmed");
    expect(fact("Evidence")).toHaveTextContent(
      "The contacts list renders nothing when the page is empty.",
    );
    const refs = within(region).getByRole("list", { name: "Code references" });
    const items = within(refs).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "web/src/features/contacts/ContactsList.tsx",
      "web/tests/e2e/contacts.spec.ts",
    ]);
    for (const item of items) expect(item.querySelector("code")).not.toBeNull();
  });

  it("links a duplicate to the original card's detail page", () => {
    render(
      <TriagePanel
        triage={triage({
          verification: {
            status: "duplicate",
            evidence: "Same empty-state request as an open card.",
            duplicate_of: ORIGINAL_ID,
            code_refs: [],
          },
        })}
        loading={false}
      />,
    );
    expect(fact("Status")).toHaveTextContent("Duplicate");
    expect(screen.getByRole("link", { name: /original card/i })).toHaveAttribute(
      "href",
      `/dev-board/${ORIGINAL_ID}`,
    );
    expect(screen.getByText("No code references.")).toBeInTheDocument();
  });

  it("labels not-reproduced and unclear verdicts in words", () => {
    const { rerender } = render(
      <TriagePanel
        triage={triage({
          verification: { status: "not_reproduced", evidence: "x", code_refs: [] },
        })}
        loading={false}
      />,
    );
    expect(fact("Status")).toHaveTextContent("Not reproduced");
    rerender(
      <TriagePanel
        triage={triage({ verification: { status: "unclear", evidence: "x", code_refs: [] } })}
        loading={false}
      />,
    );
    expect(fact("Status")).toHaveTextContent("Unclear");
    expect(screen.queryByRole("link", { name: /original card/i })).toBeNull();
  });

  it("no longer lists acceptance criteria (the planner writes them)", () => {
    render(<TriagePanel triage={triage()} loading={false} />);
    expect(screen.queryByRole("heading", { name: "Acceptance criteria" })).toBeNull();
  });

  it("explains a missing triage", () => {
    render(<TriagePanel triage={null} loading={false} />);
    expect(screen.getByText(/Not triaged yet/)).toBeInTheDocument();
  });
});
