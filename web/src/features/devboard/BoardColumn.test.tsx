import { DndContext } from "@dnd-kit/core";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BoardColumn } from "./BoardColumn";
import type { DropState } from "./columns";
import { task } from "./testing";

function renderColumn(dropState: DropState) {
  render(
    <DndContext>
      <BoardColumn
        column="ready"
        index={0}
        tasks={[task({ id: "00000000-0000-4000-8000-0000000000e1", column: "ready" })]}
        dropState={dropState}
      />
    </DndContext>,
  );
  return screen.getByRole("region", { name: "Ready" });
}

describe("BoardColumn drop states", () => {
  it("is plain when no card is being dragged", () => {
    const column = renderColumn("idle");
    expect(column).toHaveClass("column");
    expect(column).not.toHaveClass("columnValid");
    expect(column).not.toHaveClass("columnInvalid");
    expect(column).toHaveAttribute("data-drop-state", "idle");
    expect(screen.queryByText("Can’t drop here")).toBeNull();
  });

  it("marks a legal target as valid (green) without a warning", () => {
    const column = renderColumn("valid");
    expect(column).toHaveClass("columnValid");
    expect(column).not.toHaveClass("columnInvalid");
    expect(column).toHaveAttribute("data-drop-state", "valid");
    expect(column).not.toHaveAccessibleDescription(/Can’t drop here/);
    expect(screen.queryByText("Can’t drop here")).toBeNull();
  });

  it("marks an illegal target red and dashed, with the reason for screen readers only", () => {
    const column = renderColumn("invalid");
    expect(column).toHaveClass("columnInvalid");
    expect(column).not.toHaveClass("columnValid");
    expect(column).not.toHaveClass("columnOver");
    expect(column).toHaveAttribute("data-drop-state", "invalid");
    // No visible label (the human asked for the red box alone); the reason stays accessible.
    const label = screen.getByText("Can’t drop here");
    expect(label).toHaveClass("visuallyHidden");
    expect(label).not.toHaveClass("dropBlocked");
    expect(column).toHaveAccessibleDescription("Can’t drop here");
  });
});
