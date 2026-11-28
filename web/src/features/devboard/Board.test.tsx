import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { describe, expect, it, vi } from "vitest";
import { Board } from "./Board";
import type { Task } from "./model";
import { layoutBoard, task } from "./testing";

// Plain anchor, so the board renders without an app router.
vi.mock("next/link", () => ({
  default: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));

const ID = (n: number) => `00000000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`;

function renderBoard(tasks: Task[], role: "user" | "maintainer" = "maintainer") {
  layoutBoard();
  // Spread, because Biome reads a `role` prop as an ARIA role.
  const props = { tasks, role, onMove: vi.fn() };
  render(<Board {...props} />);
}

const column = (label: string) => screen.getByRole("region", { name: label });
const overlay = () => document.querySelector(".cardOverlay");

function stubReducedMotion() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

async function pickUp(title: string) {
  const user = userEvent.setup();
  screen.getByRole("article", { name: title }).focus();
  await user.keyboard("[Space]");
  await screen.findByText(`Picked up “${title}” in ${titleColumn(title)}.`);
  return user;
}

const titleColumn = (title: string) =>
  screen.getByRole("article", { name: title }).closest("section")?.querySelector("h2")
    ?.textContent ?? "";

describe("Board drag feedback", () => {
  it("shows a tilted copy of the card while it is dragged and mutes the original", async () => {
    renderBoard([task({ id: ID(1), title: "Polish chart", column: "triaged" })]);
    expect(overlay()).toBeNull();

    const user = await pickUp("Polish chart");

    await waitFor(() => expect(overlay()).not.toBeNull());
    expect(overlay()).toHaveTextContent("Polish chart");
    expect(overlay()).toHaveClass("cardOverlayTilted");
    // The original stays in its column as a placeholder.
    const original = within(column("Triaged")).getByRole("article", { name: "Polish chart" });
    expect(original).toHaveClass("cardDragging");

    await user.keyboard("[Escape]");
    await waitFor(() => expect(original).not.toHaveClass("cardDragging"));
  });

  it("does not tilt the copy when the user prefers reduced motion", async () => {
    stubReducedMotion();
    renderBoard([task({ id: ID(1), title: "Polish chart", column: "triaged" })]);
    const user = await pickUp("Polish chart");
    await waitFor(() => expect(overlay()).not.toBeNull());
    expect(overlay()).not.toHaveClass("cardOverlayTilted");
    await user.keyboard("[Escape]");
  });

  it("colours legal targets valid and illegal ones invalid, only while dragging", async () => {
    renderBoard(
      [
        task({ id: ID(1), title: "Polish chart", column: "triaged" }),
        task({ id: ID(2), title: "Fix footer", column: "backlog" }),
      ],
      "user",
    );
    for (const label of ["Backlog", "Plan review", "Ready"]) {
      expect(column(label)).toHaveAttribute("data-drop-state", "idle");
    }

    const user = await pickUp("Polish chart");

    // Maintainer-only targets are illegal for a `user`; the rest are left to the server.
    expect(column("Plan review")).toHaveClass("columnInvalid");
    expect(column("Ready")).toHaveClass("columnInvalid");
    expect(within(column("Ready")).getByText("Can’t drop here")).toBeInTheDocument();
    expect(column("Backlog")).toHaveClass("columnValid");
    expect(column("Backlog")).not.toHaveClass("columnInvalid");
    expect(column("Triaged")).toHaveAttribute("data-drop-state", "valid");

    await user.keyboard("[Escape]");
    await waitFor(() => expect(column("Ready")).toHaveAttribute("data-drop-state", "idle"));
    expect(screen.queryByText("Can’t drop here")).toBeNull();
  });

  it("colours every column but the legal ones red for an out-of-scope card", async () => {
    renderBoard([task({ id: ID(4), title: "Rotate key", column: "out_of_scope" })]);
    const user = await pickUp("Rotate key");
    expect(column("Backlog")).toHaveAttribute("data-drop-state", "valid");
    expect(column("Blocked")).toHaveAttribute("data-drop-state", "valid");
    expect(column("Ready")).toHaveAttribute("data-drop-state", "invalid");
    expect(column("Done")).toHaveAttribute("data-drop-state", "invalid");
    await user.keyboard("[Escape]");
  });
});
