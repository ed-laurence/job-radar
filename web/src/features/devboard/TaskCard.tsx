"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Link from "next/link";
import styles from "./DevBoard.module.css";
import type { Task } from "./model";

export interface TaskCardProps {
  task: Task;
  /** True while another card is being dragged and this card's column can't take it. */
  dropLocked: boolean;
}

/** One card. The handle is the drag source; the title links to the detail page. */
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
  const locked = task.column === "out_of_scope";
  const className = [styles.card, isDragging && styles.cardDragging, locked && styles.cardLocked]
    .filter(Boolean)
    .join(" ");
  const labelId = `task-${task.id}-title`;

  return (
    <li
      ref={setNodeRef}
      className={className}
      data-card-id={task.id}
      aria-labelledby={labelId}
      // Transform and transition are dynamic per drag frame (allowed by CODE-24).
      style={{ transform: CSS.Translate.toString(transform), transition }}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        className={styles.handle}
        {...attributes}
        {...listeners}
        aria-label={`Move “${task.title}”`}
      >
        <span aria-hidden="true">⠿</span>
      </button>
      <div className={styles.cardBody}>
        <Link id={labelId} href={`/dev-board/${task.id}`} className={styles.cardLink}>
          {task.title}
        </Link>
        <ul className={styles.badges} aria-label="Details">
          {locked ? <li className={`${styles.badge} ${styles.badgeDanger}`}>Locked</li> : null}
          {task.type ? <li className={styles.badge}>{task.type}</li> : null}
          {task.size ? <li className={styles.badge}>Size {task.size}</li> : null}
          {task.risk ? <li className={styles.badge}>Risk {task.risk}</li> : null}
          {task.active_run_id ? <li className={styles.badge}>Agent running</li> : null}
        </ul>
        {locked && task.out_of_scope_reason ? (
          <p className={styles.meta}>Out of scope: {task.out_of_scope_reason}</p>
        ) : null}
        {task.column === "blocked" && task.blocked_reason ? (
          <p className={styles.meta}>Blocked: {task.blocked_reason}</p>
        ) : null}
      </div>
    </li>
  );
}
