import {
  type DropAnimation,
  type DropAnimationFunction,
  defaultDropAnimationSideEffects,
} from "@dnd-kit/core";
import { CardContent } from "./CardContent";
import styles from "./DevBoard.module.css";
import type { Task } from "./model";

export interface DragPreviewProps {
  task: Task;
  /** No tilt when the user prefers reduced motion. */
  reducedMotion?: boolean;
}

/**
 * The copy of a card that follows the pointer (rendered in dnd-kit's DragOverlay): lifted and
 * slightly tilted, like Trello. Hidden from assistive tech: the live region already says where the
 * card is, and the copy must not duplicate the card's link or ids.
 */
export function DragPreview({ task, reducedMotion = false }: DragPreviewProps) {
  const className = [
    styles.card,
    styles.cardOverlay,
    !reducedMotion && styles.cardOverlayTilted,
    task.column === "out_of_scope" && styles.cardLocked,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={className} aria-hidden="true">
      <CardContent task={task} title={<span className={styles.cardLink}>{task.title}</span>} />
    </div>
  );
}

const DROP_DURATION_MS = 200;

type DropAnimationOptions = Exclude<DropAnimation, DropAnimationFunction>;

/**
 * The drop back into place: short, straightening the tilt on the way. Null (no animation) for
 * reduced motion.
 */
export function boardDropAnimation(reducedMotion: boolean): DropAnimationOptions | null {
  if (reducedMotion) return null;
  return {
    duration: DROP_DURATION_MS,
    easing: "cubic-bezier(0.2, 0, 0, 1)",
    sideEffects: defaultDropAnimationSideEffects({
      // Hide the placeholder while the copy lands on it, as dnd-kit's default does.
      styles: { active: { opacity: "0" } },
      className: { dragOverlay: styles.overlayDropping ?? "" },
    }),
  };
}
