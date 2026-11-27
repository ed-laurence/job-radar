import {
  type ClientRect,
  type DroppableContainer,
  KeyboardCode,
  type KeyboardCoordinateGetter,
} from "@dnd-kit/core";
import { TASK_COLUMNS, type TaskColumn } from "./columns";
import { columnDropId, columnFromDropId } from "./model";

const DIRECTIONS: readonly string[] = [
  KeyboardCode.Left,
  KeyboardCode.Right,
  KeyboardCode.Up,
  KeyboardCode.Down,
];

type WithData = { data: { current?: Record<string, unknown> | undefined } };

function columnOf(container: WithData | null | undefined): TaskColumn | undefined {
  const column = container?.data.current?.column;
  return TASK_COLUMNS.find((c) => c === column);
}

export interface BoardKeyboard {
  coordinateGetter: KeyboardCoordinateGetter;
  /** Where the last arrow key sent the card (a column drop id or a card id), if anywhere. */
  target: () => string | null;
  /** Call on drag start. */
  reset: () => void;
}

/**
 * Keyboard moves on the board (CODE-23): Left/Right go to the end of the previous/next column that
 * can take the card (locked columns are skipped), Up/Down move between cards in the current column.
 *
 * dnd-kit's `sortableKeyboardCoordinates` treats every droppable left of the card as a candidate,
 * which includes the card's own column (its padding starts further left), so Left only reordered
 * the card within its column instead of moving it to the previous one.
 *
 * The target is also remembered: dnd-kit only learns what the card is over after two renders, so a
 * quick Space right after an arrow would otherwise drop on the stale target (found in e2e).
 */
export function createBoardKeyboard(): BoardKeyboard {
  let target: string | null = null;

  const coordinateGetter: KeyboardCoordinateGetter = (event, { context }) => {
    if (!DIRECTIONS.includes(event.code)) return undefined;
    event.preventDefault();
    const { active, over, collisionRect, droppableRects, droppableContainers } = context;
    if (!active || !collisionRect) return undefined;

    const rectOf = (id: string): ClientRect | undefined => droppableRects.get(id);
    const at = target ?? (over ? String(over.id) : String(active.id));
    const current =
      columnFromDropId(at) ?? columnOf(droppableContainers.get(at)) ?? columnOf(active);
    if (!current) return undefined;
    const currentRect = rectOf(columnDropId(current));
    // The cards of a column in layout order (droppable rects ignore drag transforms).
    const cardsIn = (column: TaskColumn) =>
      droppableContainers
        .getEnabled()
        .filter(
          (c: DroppableContainer) => c.data.current?.type === "card" && columnOf(c) === column,
        )
        .map((c) => ({ id: String(c.id), rect: rectOf(String(c.id)) }))
        .filter((c): c is { id: string; rect: ClientRect } => c.rect !== undefined)
        .sort((a, b) => a.rect.top - b.rect.top);
    const move = (to: string, x: number, y: number) => {
      target = to;
      return { x, y };
    };

    if (event.code === KeyboardCode.Left || event.code === KeyboardCode.Right) {
      const step = event.code === KeyboardCode.Left ? -1 : 1;
      const inset = currentRect ? Math.max(0, collisionRect.left - currentRect.left) : 0;
      for (
        let i = TASK_COLUMNS.indexOf(current) + step;
        i >= 0 && i < TASK_COLUMNS.length;
        i += step
      ) {
        const column = TASK_COLUMNS[i] as TaskColumn;
        const id = columnDropId(column);
        const container = droppableContainers.get(id);
        const rect = rectOf(id);
        if (!container || container.disabled || !rect) continue;
        // Below the column's last card, so the collision picks the column and the card is appended.
        const others = cardsIn(column).filter((c) => c.id !== String(active.id));
        const last = others.at(-1);
        return move(id, rect.left + inset, last ? last.rect.bottom + 1 : rect.top);
      }
      return undefined;
    }

    const cards = cardsIn(current);
    if (cards.length === 0) return undefined;
    let index = cards.findIndex((c) => c.id === at);
    if (index < 0) index = at === columnDropId(current) ? cards.length : 0;
    const x = cards[0]?.rect.left ?? collisionRect.left;

    if (event.code === KeyboardCode.Up) {
      const above = cards[index - 1];
      return above ? move(above.id, x, above.rect.top) : undefined;
    }
    const below = cards[index + 1];
    if (below) return move(below.id, x, below.rect.top);
    // Past the last card: over the column itself, so the card is appended.
    const last = cards.at(-1);
    if (!last || index >= cards.length) return undefined;
    return move(columnDropId(current), x, last.rect.bottom + 1);
  };

  return {
    coordinateGetter,
    target: () => target,
    reset: () => {
      target = null;
    },
  };
}
