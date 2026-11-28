import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { boardDropAnimation, DragPreview } from "./DragPreview";
import { task } from "./testing";

const ID = "00000000-0000-4000-8000-0000000000d1";

describe("DragPreview", () => {
  it("renders a lifted, tilted copy of the card that assistive tech ignores", () => {
    const { container } = render(
      <DragPreview task={task({ id: ID, title: "Polish chart", body: "Tidy axes" })} />,
    );
    const preview = container.firstElementChild;
    expect(preview).toHaveClass("card", "cardOverlay", "cardOverlayTilted");
    expect(preview).toHaveAttribute("aria-hidden", "true");
    expect(preview).toHaveTextContent("Polish chart");
    expect(preview).toHaveTextContent("Tidy axes");
    // A copy, not a second card: no link, no duplicate ids, nothing focusable.
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.querySelector("[id]")).toBeNull();
    expect(container.querySelector("[tabindex]")).toBeNull();
  });

  it("does not tilt when the user prefers reduced motion", () => {
    const { container } = render(
      <DragPreview task={task({ id: ID, title: "Polish chart" })} reducedMotion />,
    );
    const preview = container.firstElementChild;
    expect(preview).toHaveClass("cardOverlay");
    expect(preview).not.toHaveClass("cardOverlayTilted");
  });
});

describe("boardDropAnimation", () => {
  it("animates the drop back in about 200ms", () => {
    const animation = boardDropAnimation(false);
    expect(animation).not.toBeNull();
    expect(animation?.duration).toBe(200);
    expect(animation?.sideEffects).toBeTypeOf("function");
  });

  it("skips the drop animation for reduced motion", () => {
    expect(boardDropAnimation(true)).toBeNull();
  });
});
