import { vi } from "vitest";
import type { Schemas } from "@/lib/api";

/** Task fixtures and jsdom layout for the dev-board tests. Render helpers live in `@/lib/testing`. */
export function task(overrides: Partial<Schemas["Task"]> & { id: string }): Schemas["Task"] {
  return {
    title: `Task ${overrides.id.slice(-2)}`,
    column: "backlog",
    rank: "a",
    version: 1,
    created_at: "2026-11-25T00:00:00Z",
    updated_at: "2026-11-25T00:00:00Z",
    ...overrides,
  };
}

/** Lays the board out in jsdom (which has no layout) so dnd-kit's keyboard sensor can navigate. */
export function layoutBoard(): void {
  // 11 columns fit in jsdom's 1024px viewport, so the sensor moves instead of scrolling.
  const COLUMN_WIDTH = 90;
  const CARD_HEIGHT = 60;
  const rect = (x: number, y: number, width: number, height: number) =>
    ({
      x,
      y,
      left: x,
      top: y,
      width,
      height,
      right: x + width,
      bottom: y + height,
      toJSON() {},
    }) as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const columnEl = this.closest("[data-column-index]");
    const column = Number(columnEl?.getAttribute("data-column-index") ?? -1);
    if (column < 0) return rect(0, 0, 0, 0);
    const x = column * COLUMN_WIDTH;
    const card = this.closest("[data-card-id]");
    if (!card) return rect(x, 0, COLUMN_WIDTH - 10, 700);
    const index = [...(card.parentElement?.children ?? [])].indexOf(card);
    return rect(x + 5, 80 + index * (CARD_HEIGHT + 10), COLUMN_WIDTH - 20, CARD_HEIGHT);
  });
}
