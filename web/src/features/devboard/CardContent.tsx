import type { ReactNode } from "react";
import styles from "./DevBoard.module.css";
import type { Task } from "./model";
import { TaskBadges } from "./TaskBadges";

export interface CardContentProps {
  task: Task;
  /** The title element: a link on the board, plain text on the drag preview. */
  title: ReactNode;
}

/** What a card shows, shared by the card on the board and its drag preview. */
export function CardContent({ task, title }: CardContentProps) {
  return (
    <>
      {title}
      {task.body ? (
        <p className={styles.cardDescription} title={task.body}>
          {task.body}
        </p>
      ) : null}
      <TaskBadges task={task} />
      {task.column === "out_of_scope" && task.out_of_scope_reason ? (
        <p className={`${styles.meta} ${styles.clamp}`} title={task.out_of_scope_reason}>
          Out of scope: {task.out_of_scope_reason}
        </p>
      ) : null}
      {task.column === "blocked" && task.blocked_reason ? (
        <p className={`${styles.meta} ${styles.clamp}`} title={task.blocked_reason}>
          Blocked: {task.blocked_reason}
        </p>
      ) : null}
    </>
  );
}
