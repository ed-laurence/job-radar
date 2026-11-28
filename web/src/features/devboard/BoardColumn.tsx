"use client";

import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { COLUMN_LABELS, type DropState, type TaskColumn } from "./columns";
import styles from "./DevBoard.module.css";
import { columnDropId, type Task } from "./model";
import { TaskCard } from "./TaskCard";

export interface BoardColumnProps {
  column: TaskColumn;
  index: number;
  tasks: Task[];
  /**
   * While a card is dragged: `valid` if it may go here, `invalid` if the board rules it out up
   * front (shown before the drop, not after a 403/422). `idle` when nothing is dragged.
   */
  dropState: DropState;
  /** A short visible note, e.g. "Maintainer only" for a `user`. */
  note?: string | undefined;
}

export function BoardColumn({ column, index, tasks, dropState, note }: BoardColumnProps) {
  const locked = dropState === "invalid";
  const { setNodeRef, isOver } = useDroppable({
    id: columnDropId(column),
    data: { type: "column", column },
    disabled: locked,
  });
  const headingId = `column-${column}`;
  const blockedId = `column-${column}-blocked`;
  const className = [
    styles.column,
    dropState === "valid" && styles.columnValid,
    dropState === "valid" && isOver && styles.columnOver,
    locked && styles.columnInvalid,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section
      ref={setNodeRef}
      className={className}
      aria-labelledby={headingId}
      // Colour is not the only cue: a locked column is dashed with a not-allowed cursor, and
      // its region tells screen readers why.
      aria-describedby={locked ? blockedId : undefined}
      data-drop-state={dropState}
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
      {locked ? (
        <p id={blockedId} className={styles.visuallyHidden}>
          Can’t drop here
        </p>
      ) : null}
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
