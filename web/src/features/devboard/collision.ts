import {
  type CollisionDetection,
  closestCenter,
  pointerWithin,
  rectIntersection,
} from "@dnd-kit/core";

/**
 * Column first, then card: find the column under the pointer (or the dragged rect, for the
 * keyboard), then the closest card in it. Below a column's last card the column itself wins, so
 * the card is appended. `closestCorners` alone picks a card in the old column when columns are tall.
 */
export const columnFirstCollision: CollisionDetection = (args) => {
  const columns = args.droppableContainers.filter((c) => c.data.current?.type === "column");
  const scoped = { ...args, droppableContainers: columns };
  const underPointer = args.pointerCoordinates ? pointerWithin(scoped) : [];
  const hit = (underPointer.length ? underPointer : rectIntersection(scoped))[0];
  if (!hit) return [];
  const column = args.droppableContainers.find((c) => c.id === hit.id)?.data.current?.column;
  const cards = args.droppableContainers.filter(
    (c) => c.data.current?.type === "card" && c.data.current.column === column,
  );
  if (cards.length === 0) return [hit];
  const nearest = closestCenter({ ...args, droppableContainers: cards })[0];
  if (!nearest) return [hit];
  const lastBottom = Math.max(
    ...cards.map((c) => args.droppableRects.get(c.id)?.bottom ?? Number.NEGATIVE_INFINITY),
  );
  const y = args.pointerCoordinates?.y ?? args.collisionRect.top + args.collisionRect.height / 2;
  return y > lastBottom ? [hit] : [nearest];
};
