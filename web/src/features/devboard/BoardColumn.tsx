"use client";

import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { COLUMN_LABELS, type TaskColumn } from "./columns";
import styles from "./DevBoard.module.css";
import { columnDropId, type Task } from "./model";
import { TaskCard } from "./TaskCard";

export interface BoardColumnProps {
  column: TaskColumn;
  index: number;
  tasks: Task[];
  /** The dragged card can't be dropped here (shown before the drop, not after a 403/422). */
  locked: boolean;
  /** A short visible note, e.g. "Maintainer only" for a `user`. */
  note?: string | undefined;
}

export function BoardColumn({ column, index, tasks, locked, note }: BoardColumnProps) {
  const { setNodeRef, isOver } = useDroppable({
    id: columnDropId(column),
    data: { type: "column", column },
    disabled: locked,
  });
  const headingId = `column-${column}`;
  const className = [
    styles.column,
    isOver && !locked && styles.columnOver,
    locked && styles.columnLocked,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section
      ref={setNodeRef}
      className={className}
      aria-labelledby={headingId}
      data-column={column}
      data-column-index={index}
    >
      <header className={styles.columnHeader}>
        <h2 id={headingId} className={styles.columnTitle}>
          {COLUMN_LABELS[column]}
        </h2>
        <span className={styles.count}>
          {tasks.length} {tasks.length === 1 ? "card" : "cards"}
        </span>
      </header>
      {note ? <p className={styles.hint}>{note}</p> : null}
      {locked ? <p className={styles.hint}>Can’t drop here</p> : null}
      <SortableContext items={tasks.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <ol className={styles.cards}>
          {tasks.map((t) => (
            <TaskCard key={t.id} task={t} dropLocked={locked} />
          ))}
        </ol>
      </SortableContext>
    </section>
  );
}
