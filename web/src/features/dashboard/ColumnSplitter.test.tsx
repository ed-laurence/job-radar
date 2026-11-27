import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import {
  ColumnSplitter,
  clampSplit,
  DEFAULT_SPLIT,
  MAX_SPLIT,
  MIN_SPLIT,
  readSplit,
  SPLIT_KEY,
  useColumnSplit,
} from "./ColumnSplitter";

function Harness() {
  const [split, setSplit] = useColumnSplit();
  const [layout, setLayout] = useState<HTMLDivElement | null>(null);
  return (
    <div ref={setLayout} data-testid="layout" data-split={split}>
      <ColumnSplitter value={split} onChange={setSplit} container={layout} />
    </div>
  );
}

// jsdom has no PointerEvent, so fireEvent.pointer* would drop clientX and pointerId.
if (!("PointerEvent" in window)) {
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  Object.defineProperty(window, "PointerEvent", { value: TestPointerEvent });
}

afterEach(() => window.localStorage.clear());

describe("clampSplit / readSplit", () => {
  it("keeps the campaign column between its limits", () => {
    expect(clampSplit(10)).toBe(MIN_SPLIT);
    expect(clampSplit(90)).toBe(MAX_SPLIT);
    expect(clampSplit(40.26)).toBe(40.3);
    expect(clampSplit(Number.NaN)).toBe(DEFAULT_SPLIT);
  });

  it("falls back to the default for missing or junk stored values", () => {
    expect(readSplit(null)).toBe(DEFAULT_SPLIT);
    expect(readSplit("wide")).toBe(DEFAULT_SPLIT);
    expect(readSplit("55")).toBe(55);
    expect(readSplit("99")).toBe(MAX_SPLIT);
  });
});

describe("ColumnSplitter", () => {
  it("is a labelled vertical separator reporting the campaign column's share", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator", { name: "Resize the campaign column" });
    expect(sep).toHaveAttribute("aria-orientation", "vertical");
    expect(sep).toHaveAttribute("aria-valuemin", String(MIN_SPLIT));
    expect(sep).toHaveAttribute("aria-valuemax", String(MAX_SPLIT));
    expect(sep).toHaveAttribute("aria-valuenow", String(DEFAULT_SPLIT));
  });

  it("resizes with the arrow keys, Home and End, and remembers the width", async () => {
    render(<Harness />);
    const sep = screen.getByRole("separator", { name: "Resize the campaign column" });
    sep.focus();
    await userEvent.keyboard("{ArrowRight}{ArrowRight}");
    expect(sep).toHaveAttribute("aria-valuenow", String(DEFAULT_SPLIT + 4));
    await userEvent.keyboard("{ArrowLeft}");
    expect(sep).toHaveAttribute("aria-valuenow", String(DEFAULT_SPLIT + 2));
    expect(window.localStorage.getItem(SPLIT_KEY)).toBe(String(DEFAULT_SPLIT + 2));
    await userEvent.keyboard("{Home}");
    expect(sep).toHaveAttribute("aria-valuenow", String(MIN_SPLIT));
    await userEvent.keyboard("{End}");
    expect(sep).toHaveAttribute("aria-valuenow", String(MAX_SPLIT));
  });

  it("resets to the default on double-click", async () => {
    window.localStorage.setItem(SPLIT_KEY, "50");
    render(<Harness />);
    const sep = screen.getByRole("separator", { name: "Resize the campaign column" });
    expect(sep).toHaveAttribute("aria-valuenow", "50");
    await userEvent.dblClick(sep);
    expect(sep).toHaveAttribute("aria-valuenow", String(DEFAULT_SPLIT));
  });

  it("follows the pointer across the layout while dragging", () => {
    render(<Harness />);
    const layout = screen.getByTestId("layout");
    layout.getBoundingClientRect = () =>
      ({ left: 100, width: 1000, top: 0, height: 0, right: 1100, bottom: 0 }) as DOMRect;
    const sep = screen.getByRole("separator", { name: "Resize the campaign column" });
    fireEvent.pointerDown(sep, { clientX: 517, pointerId: 1, button: 0 });
    fireEvent.pointerMove(sep, { clientX: 600, pointerId: 1 });
    expect(sep).toHaveAttribute("aria-valuenow", "50");
    fireEvent.pointerUp(sep, { clientX: 600, pointerId: 1 });
    fireEvent.pointerMove(sep, { clientX: 300, pointerId: 1 });
    expect(sep).toHaveAttribute("aria-valuenow", "50");
    expect(window.localStorage.getItem(SPLIT_KEY)).toBe("50");
  });
});
