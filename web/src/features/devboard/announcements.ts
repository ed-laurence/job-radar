import type { Announcements, ScreenReaderInstructions, UniqueIdentifier } from "@dnd-kit/core";
import { COLUMN_LABELS } from "./columns";
import { columnFromDropId, type Task } from "./model";

export const SCREEN_READER_INSTRUCTIONS: ScreenReaderInstructions = {
  draggable:
    "To move a card, press Space or Enter to pick it up. Use the arrow keys to move it between " +
    "columns and within a column. Press Space or Enter to drop it, or Escape to cancel.",
};

/**
 * dnd-kit live-region messages (CODE-23). `getTasks` returns the board as it was at drag start;
 * `getDropTarget` is where the keyboard sent the card, which wins over dnd-kit's `over` on drop.
 */
export function buildAnnouncements(
  getTasks: () => readonly Task[],
  getDropTarget: () => string | null = () => null,
): Announcements {
  const find = (id: UniqueIdentifier) => getTasks().find((t) => t.id === String(id));
  const name = (id: UniqueIdentifier) => `“${find(id)?.title ?? "card"}”`;
  const where = (id: UniqueIdentifier | undefined): string | undefined => {
    if (id === undefined) return undefined;
    const column = columnFromDropId(String(id));
    if (column) return COLUMN_LABELS[column];
    const card = find(id);
    return card ? `${name(id)} in ${COLUMN_LABELS[card.column]}` : undefined;
  };
  const home = (id: UniqueIdentifier) => {
    const card = find(id);
    return card ? COLUMN_LABELS[card.column] : "its column";
  };

  return {
    onDragStart: ({ active }) => `Picked up ${name(active.id)} in ${home(active.id)}.`,
    onDragOver: ({ active, over }) => {
      const target = where(over?.id);
      return target
        ? `${name(active.id)} is over ${target}.`
        : `${name(active.id)} is not over a column.`;
    },
    onDragEnd: ({ active, over }) => {
      const overId = getDropTarget() ?? (over ? String(over.id) : undefined);
      const column = overId ? columnFromDropId(overId) : undefined;
      const card = overId ? find(overId) : undefined;
      const label = column ? COLUMN_LABELS[column] : card ? COLUMN_LABELS[card.column] : undefined;
      return label
        ? `Dropped ${name(active.id)} in ${label}. Saving…`
        : `Dropped ${name(active.id)}. It stays in ${home(active.id)}.`;
    },
    onDragCancel: ({ active }) =>
      `Move cancelled. ${name(active.id)} is back in ${home(active.id)}.`,
  };
}
