import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskBadges } from "./TaskBadges";
import { task } from "./testing";

const ID = "00000000-0000-4000-8000-0000000000b1";

function badges(): (string | null)[] {
  return within(screen.getByRole("list", { name: "Details" }))
    .getAllByRole("listitem")
    .map((li) => li.textContent);
}

describe("TaskBadges", () => {
  it("shows the priority as text, with a spoken label", () => {
    render(<TaskBadges task={task({ id: ID, column: "ready", priority: "P0", type: "bug" })} />);
    expect(badges()).toContain("Priority P0");
    expect(screen.getByText("P0")).toBeVisible();
  });

  it("shows the route in Triaged as a word", () => {
    const { rerender } = render(
      <TaskBadges task={task({ id: ID, column: "triaged", priority: "P2", route: "next" })} />,
    );
    expect(badges()).toContain("Next");
    rerender(<TaskBadges task={task({ id: ID, column: "triaged", route: "later" })} />);
    expect(badges()).toContain("Later");
  });

  it("hides the route outside Triaged", () => {
    render(<TaskBadges task={task({ id: ID, column: "ready", priority: "P3", route: "later" })} />);
    expect(badges()).not.toContain("Later");
  });

  it("omits the priority until triage sets one", () => {
    render(<TaskBadges task={task({ id: ID, column: "backlog", type: "feature" })} />);
    expect(badges().some((b) => b?.includes("Priority"))).toBe(false);
  });

  it("keeps type, size and risk badges", () => {
    render(
      <TaskBadges
        task={task({ id: ID, column: "ready", type: "security", size: "M", risk: "high" })}
      />,
    );
    expect(badges()).toEqual(expect.arrayContaining(["security", "Size M", "Risk high"]));
  });
});

function badge(text: string | RegExp): HTMLElement {
  const el = within(screen.getByRole("list", { name: "Details" }))
    .getAllByRole("listitem")
    .find((li) =>
      typeof text === "string" ? li.textContent === text : text.test(li.textContent ?? ""),
    );
  if (!el) throw new Error(`no badge ${String(text)}`);
  return el;
}

describe("TaskBadges colours", () => {
  it.each([
    ["P0", "danger"],
    ["P1", "hot"],
    ["P2", "warm"],
    ["P3", "cold"],
  ] as const)("colours priority %s as %s", (priority, tone) => {
    render(<TaskBadges task={task({ id: ID, column: "ready", priority })} />);
    expect(badge(`Priority ${priority}`)).toHaveClass("badge", toneClass(tone));
  });

  it.each([
    ["now", "Now", "hot"],
    ["next", "Next", "warm"],
    ["later", "Later", "cold"],
  ] as const)("colours route %s as %s", (route, label, tone) => {
    render(<TaskBadges task={task({ id: ID, column: "triaged", route })} />);
    expect(badge(label)).toHaveClass(toneClass(tone));
  });

  it.each([
    ["high", "danger"],
    ["medium", "warm"],
    ["low", "accent"],
  ] as const)("colours risk %s as %s", (risk, tone) => {
    render(<TaskBadges task={task({ id: ID, column: "ready", risk })} />);
    expect(badge(`Risk ${risk}`)).toHaveClass(toneClass(tone));
  });

  it.each([
    ["security", "danger"],
    ["bug", "hot"],
    ["feature", "accent"],
    ["performance", "mission"],
    ["chore", "cold"],
    ["docs", "cold"],
  ] as const)("colours type %s as %s", (type, tone) => {
    render(<TaskBadges task={task({ id: ID, column: "ready", type })} />);
    expect(badge(type)).toHaveClass(toneClass(tone));
  });

  it("keeps size neutral and marks locked cards as danger", () => {
    render(<TaskBadges task={task({ id: ID, column: "out_of_scope", size: "L" })} />);
    expect(badge("Size L")).toHaveClass("badge", "badgeNeutral");
    expect(badge("Locked")).toHaveClass("badgeDanger");
  });

  it("shows areas and labels as pills with spoken prefixes", () => {
    render(
      <TaskBadges
        task={task({ id: ID, column: "ready", areas: ["web"], labels: ["ux", "a11y"] })}
      />,
    );
    expect(badge("Area web")).toHaveClass("badgeMission");
    expect(badge("Label ux")).toHaveClass("badgeNeutral");
    expect(badge("Label a11y")).toHaveClass("badgeNeutral");
  });

  it("caps labels at three and says how many are hidden", () => {
    render(
      <TaskBadges task={task({ id: ID, column: "ready", labels: ["a", "b", "c", "d", "e"] })} />,
    );
    expect(badges().filter((b) => b?.startsWith("Label "))).toEqual([
      "Label a",
      "Label b",
      "Label c",
    ]);
    expect(badge(/^\+2/)).toHaveTextContent("+2 more labels");
    expect(badge(/^\+2/)).toHaveAttribute("title", "d, e");
  });
});

function toneClass(tone: string): string {
  return `badge${tone.charAt(0).toUpperCase()}${tone.slice(1)}`;
}
