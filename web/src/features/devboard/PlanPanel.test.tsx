import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { PlanPanel } from "./PlanPanel";

const FIRST_TEST = "web/tests/e2e/contacts.spec.ts::shows empty state";

function plan(overrides: Partial<Schemas["Plan"]> = {}): Schemas["Plan"] {
  return {
    task_id: "00000000-0000-4000-8000-0000000000b1",
    version: 1,
    mode: "single",
    subtasks: [{ id: "s1", title: "Empty state", agent: "web-writer", lease_paths: [] }],
    acceptance_criteria: [
      { test_id: FIRST_TEST, expectation: "Shows a hint" },
      {
        test_id: "web/src/features/contacts/ContactsList.test.tsx::links to create",
        expectation: "Offers a link to add a contact",
      },
    ],
    acceptance_tests: [],
    tests_to_change: ["web/tests/e2e/contacts.spec.ts"],
    budget: { max_usd: 2, max_minutes: 20 },
    risk: "low",
    tests_approved: false,
    plan_approved: false,
    created_at: "2026-11-25T00:00:00Z",
    ...overrides,
  };
}

function renderPanel(p: Schemas["Plan"]) {
  return render(
    <PlanPanel
      plan={p}
      labels={[]}
      loading={false}
      canApprove
      approving={false}
      onApprove={vi.fn()}
    />,
  );
}

describe("PlanPanel", () => {
  it("lists the planner's acceptance criteria with monospace test ids", () => {
    renderPanel(plan());
    const region = screen.getByRole("region", { name: "Plan" });
    const list = within(region).getByRole("list", { name: "Acceptance criteria" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const first = items[0] as HTMLElement;
    expect(first).toHaveTextContent(FIRST_TEST);
    expect(first).toHaveTextContent("Shows a hint");
    expect(within(first).getByText(FIRST_TEST)).toHaveClass("mono");
  });

  it("shows the criteria above the existing tests this plan changes", () => {
    renderPanel(plan());
    const criteria = screen.getByRole("heading", { name: "Acceptance criteria" });
    const changes = screen.getByRole("heading", { name: "Existing tests this plan changes" });
    expect(
      criteria.compareDocumentPosition(changes) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("says when the planner wrote no criteria", () => {
    renderPanel(plan({ acceptance_criteria: [] }));
    expect(screen.getByRole("heading", { name: "Acceptance criteria" })).toBeVisible();
    expect(screen.getByText("No acceptance criteria yet.")).toBeInTheDocument();
  });
});
