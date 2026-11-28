"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Link from "next/link";
import type { KeyboardEvent, MouseEvent } from "react";
import { CardContent } from "./CardContent";
import { guardClickAfterDrag } from "./clickGuard";
import styles from "./DevBoard.module.css";
import type { Task } from "./model";

export interface TaskCardProps {
  task: Task;
  /** True while another card is being dragged and this card's column can't take it. */
  dropLocked: boolean;
}

/**
 * One card. The whole card is the drag source (press and drag, or focus it and press Space or
 * Enter); a plain click on the title still opens the detail page. While it is dragged, it stays
 * in place as a muted placeholder and a copy (DragPreview) follows the pointer.
 */
export function TaskCard({ task, dropLocked }: TaskCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: task.id,
    data: { type: "card", column: task.column },
    disabled: { draggable: false, droppable: dropLocked },
  });
  // The card is an <article>, not a button: it contains a link, and buttons can't hold
  // interactive content. So drop dnd-kit's button role and the button-only `aria-pressed`.
  const { role: _role, "aria-pressed": _pressed, ...cardAttributes } = attributes;
  const locked = task.column === "out_of_scope";
  const className = [styles.card, isDragging && styles.cardDragging, locked && styles.cardLocked]
    .filter(Boolean)
    .join(" ");
  const labelId = `task-${task.id}-title`;

  const onMouseDown = (event: MouseEvent<HTMLElement>) => {
    guardClickAfterDrag(event.currentTarget, event.clientX, event.clientY);
    listeners?.onMouseDown?.(event);
  };
  // Only the card itself picks up from the keyboard, so Enter on the focused title still follows it.
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target === event.currentTarget) listeners?.onKeyDown?.(event);
  };

  return (
    <li
      ref={setNodeRef}
      className={styles.cardItem}
      data-card-id={task.id}
      aria-labelledby={labelId}
      // Transform and transition are dynamic per drag frame (allowed by CODE-24).
      style={{ transform: CSS.Translate.toString(transform), transition }}
    >
      <article
        ref={setActivatorNodeRef}
        className={className}
        {...cardAttributes}
        {...listeners}
        onMouseDown={onMouseDown}
        onKeyDown={onKeyDown}
        aria-labelledby={labelId}
      >
        <CardContent
          task={task}
          title={
            <Link id={labelId} href={`/dev-board/${task.id}`} className={styles.cardLink}>
              {task.title}
            </Link>
          }
        />
      </article>
    </li>
  );
}
