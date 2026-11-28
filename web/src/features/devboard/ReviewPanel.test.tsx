import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TaskReview } from "./api";
import { ReviewPanel } from "./ReviewPanel";

const REVIEW: TaskReview = {
  task_id: "00000000-0000-4000-8000-0000000000b1",
  run_id: "00000000-0000-4000-8000-0000000000f3",
  round: 2,
  verdict: "changes_requested",
  summary: "The empty state works but the layering is off.",
  created_at: "2026-11-25T02:00:00Z",
  findings: [
    {
      severity: "NIT",
      path: "web/src/features/contacts/Empty.tsx",
      line: 4,
      evidence: "The heading level skips h2.",
      fix: "Use an h2.",
    },
    {
      severity: "BLOCKER",
      rubric_id: "ARCH-03",
      path: "web/src/features/contacts/api.ts",
      line: 12,
      evidence: "Fetches with raw fetch instead of the generated client.",
      fix: "Use useApiClient.",
    },
    {
      severity: "NIT",
      downgraded: true,
      path: "web/src/features/contacts/List.tsx",
      evidence: "Might re-render too often.",
    },
    {
      severity: "BLOCKER",
      test_id: "web/tests/contacts.spec.ts::shows empty state",
      repro: "Open contacts with none saved.",
      path: "web/src/features/contacts/List.tsx",
      line: 30,
      evidence: "The test fails: nothing renders.",
      fix: "Render Empty when the list is empty.",
    },
  ],
};

/** The findings themselves, not the citation items nested inside them. */
function findings(): HTMLElement[] {
  const list = screen.getByRole("list", { name: "Findings" });
  return within(list)
    .getAllByRole("listitem")
    .filter((li) => li.parentElement === list);
}

describe("ReviewPanel", () => {
  it("shows the round, the verdict as text and the summary", () => {
    render(<ReviewPanel review={REVIEW} loading={false} />);
    const panel = screen.getByRole("region", { name: "Agent review" });
    expect(within(panel).getByText("Round 2")).toBeInTheDocument();
    expect(within(panel).getByText("Changes requested")).toBeInTheDocument();
    expect(within(panel).getByText(REVIEW.summary)).toBeInTheDocument();
  });

  it.each([
    ["approved", "Approved"],
    ["needs_decision", "Needs decision"],
  ] as const)("labels the %s verdict", (verdict, label) => {
    render(<ReviewPanel review={{ ...REVIEW, verdict }} loading={false} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("lists blockers first, keeping the order within each severity", () => {
    render(<ReviewPanel review={REVIEW} loading={false} />);
    const items = findings();
    expect(items.map((li) => within(li).getByText(/^(BLOCKER|NIT)$/).textContent)).toEqual([
      "BLOCKER",
      "BLOCKER",
      "NIT",
      "NIT",
    ]);
    expect(within(items[0] as HTMLElement).getByText(/ARCH-03/)).toBeInTheDocument();
    expect(within(items[1] as HTMLElement).getByText(/contacts\.spec/)).toBeInTheDocument();
  });

  it("shows each finding's citation, location, evidence and fix", () => {
    render(<ReviewPanel review={REVIEW} loading={false} />);
    const [rubric, test] = findings() as [HTMLElement, HTMLElement];
    expect(within(rubric).getByText("Rubric ARCH-03")).toBeInTheDocument();
    const location = within(rubric).getByText("web/src/features/contacts/api.ts:12");
    expect(location.tagName).toBe("CODE");
    expect(within(rubric).getByText(/raw fetch/)).toBeInTheDocument();
    expect(within(rubric).getByText(/Use useApiClient/)).toBeInTheDocument();
    expect(
      within(test).getByText("Test web/tests/contacts.spec.ts::shows empty state"),
    ).toBeInTheDocument();
    expect(within(test).getByText(/Repro: Open contacts with none saved/)).toBeInTheDocument();
  });

  it("notes a downgraded finding and shows a path without a line", () => {
    render(<ReviewPanel review={REVIEW} loading={false} />);
    const downgraded = findings()[3] as HTMLElement;
    expect(within(downgraded).getByText("downgraded: no citation")).toBeInTheDocument();
    expect(within(downgraded).getByText("web/src/features/contacts/List.tsx")).toBeInTheDocument();
  });

  it("says when there is no review yet", () => {
    render(<ReviewPanel review={null} loading={false} />);
    expect(screen.getByText("No agent review yet.")).toBeInTheDocument();
  });

  it("says when the review found nothing", () => {
    render(
      <ReviewPanel review={{ ...REVIEW, verdict: "approved", findings: [] }} loading={false} />,
    );
    expect(screen.getByText("No findings.")).toBeInTheDocument();
  });
});
