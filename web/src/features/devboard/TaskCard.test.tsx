import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { describe, expect, it, vi } from "vitest";
import { Board } from "./Board";
import type { Task } from "./model";
import { task } from "./testing";

// Plain anchor, so a click's default action is observable without an app router.
vi.mock("next/link", () => ({
  default: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));

const ID = "00000000-0000-4000-8000-0000000000c1";
const TITLE = "Polish chart";

function renderCard(overrides: Partial<Task> = {}) {
  // Spread, because Biome reads a `role` prop as an ARIA role.
  const props = {
    tasks: [task({ id: ID, title: TITLE, column: "ready", ...overrides })],
    role: "maintainer" as const,
    onMove: vi.fn(),
  };
  render(<Board {...props} />);
  return {
    card: screen.getByRole("article", { name: TITLE }),
    link: screen.getByRole("link", { name: TITLE }),
  };
}

describe("TaskCard", () => {
  it("has no separate drag handle: the whole card is the drag source", () => {
    const { card, link } = renderCard();
    expect(screen.queryByRole("button", { name: /Move/ })).toBeNull();
    expect(card).toHaveAttribute("tabindex", "0");
    expect(card).toHaveAttribute("aria-roledescription", "sortable");
    expect(card).toHaveAccessibleDescription(/press Space or Enter to pick it up/i);
    expect(card).not.toHaveAttribute("aria-pressed");
    expect(card).toContainElement(link);
  });

  it("still opens the detail page on a plain click", () => {
    const { link } = renderCard();
    expect(link).toHaveAttribute("href", `/dev-board/${ID}`);
    fireEvent.mouseDown(link, { clientX: 20, clientY: 20 });
    fireEvent.mouseUp(link, { clientX: 21, clientY: 20 });
    // fireEvent returns false when the default action (navigation) was prevented.
    expect(fireEvent.click(link, { clientX: 21, clientY: 20 })).toBe(true);
  });

  it("does not follow the link when the press ends a drag", () => {
    const { link } = renderCard();
    fireEvent.mouseDown(link, { clientX: 20, clientY: 20 });
    fireEvent.mouseUp(link, { clientX: 60, clientY: 20 });
    expect(fireEvent.click(link, { clientX: 60, clientY: 20 })).toBe(false);
    // The guard is one-shot: the next plain click navigates again.
    fireEvent.mouseDown(link, { clientX: 60, clientY: 20 });
    fireEvent.mouseUp(link, { clientX: 60, clientY: 20 });
    expect(fireEvent.click(link, { clientX: 60, clientY: 20 })).toBe(true);
  });

  it("picks the card up from the keyboard only when the card itself has focus", async () => {
    const user = userEvent.setup();
    const { card, link } = renderCard();
    link.focus();
    await user.keyboard("[Space]");
    expect(screen.queryByText(/Picked up/)).toBeNull();
    card.focus();
    await user.keyboard("[Space]");
    expect(await screen.findByText(`Picked up “${TITLE}” in Ready.`)).toBeInTheDocument();
    await user.keyboard("[Escape]");
  });

  it("clamps the description to a few lines but keeps the full text", () => {
    const body = "A long description. ".repeat(40).trim();
    renderCard({ body });
    const description = screen.getByText(body);
    expect(description).toHaveClass("cardDescription");
    expect(description).toHaveAttribute("title", body);
  });

  it("omits the description when the task has none", () => {
    renderCard();
    expect(document.querySelector(".cardDescription")).toBeNull();
  });
});
