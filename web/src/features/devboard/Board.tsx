"use client";

import {
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useMemo, useRef, useState } from "react";
import { buildAnnouncements, SCREEN_READER_INSTRUCTIONS } from "./announcements";
import { BoardColumn } from "./BoardColumn";
import { DRAG_DISTANCE_PX } from "./clickGuard";
import { columnFirstCollision } from "./collision";
import {
  type AppRole,
  dropStateFor,
  isTargetLocked,
  MAINTAINER_TARGETS,
  TASK_COLUMNS,
  type TaskColumn,
} from "./columns";
import styles from "./DevBoard.module.css";
import { boardDropAnimation, DragPreview } from "./DragPreview";
import { createBoardKeyboard } from "./keyboard";
import { groupByColumn, resolveDrop, type Task } from "./model";
import { useReducedMotion } from "./useReducedMotion";

export interface BoardProps {
  tasks: Task[];
  role: AppRole | undefined;
  onMove: (task: Task, toColumn: TaskColumn, rank: string) => void;
}

/**
 * The Kanban board: pointer and keyboard drag (dnd-kit) with screen-reader announcements. While a
 * card is dragged, columns it may move to are marked valid and the rest invalid (`isTargetLocked`).
 */
export function Board({ tasks, role, onMove }: BoardProps) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const tasksRef = useRef(tasks);
  const [keyboard] = useState(createBoardKeyboard);
  tasksRef.current = tasks;
  const sensors = useSensors(
    // The whole card drags. A mouse drag starts after a few pixels, so clicks and text selection
    // still work; touch waits for a short press, so a swipe still scrolls the board.
    useSensor(MouseSensor, { activationConstraint: { distance: DRAG_DISTANCE_PX } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: DRAG_DISTANCE_PX } }),
    useSensor(KeyboardSensor, { coordinateGetter: keyboard.coordinateGetter }),
  );
  const announcements = useMemo(
    () => buildAnnouncements(() => tasksRef.current, keyboard.target),
    [keyboard],
  );
  const grouped = useMemo(() => groupByColumn(tasks), [tasks]);
  const active = activeId ? tasks.find((t) => t.id === activeId) : undefined;
  const reducedMotion = useReducedMotion();
  const dropAnimation = useMemo(() => boardDropAnimation(reducedMotion), [reducedMotion]);

  const onDragStart = ({ active: a }: DragStartEvent) => {
    keyboard.reset();
    setActiveId(String(a.id));
  };
  const onDragEnd = ({ active: a, over }: DragEndEvent) => {
    setActiveId(null);
    const task = tasksRef.current.find((t) => t.id === String(a.id));
    // A keyboard drop goes where the last arrow key sent it, even if dnd-kit has not caught up.
    const overId = keyboard.target() ?? (over ? String(over.id) : null);
    const drop = resolveDrop(tasksRef.current, String(a.id), overId);
    if (task && drop && !isTargetLocked(task, drop.toColumn, role)) {
      onMove(task, drop.toColumn, drop.rank);
    }
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={columnFirstCollision}
      accessibility={{ announcements, screenReaderInstructions: SCREEN_READER_INSTRUCTIONS }}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <div className={styles.board}>
        {TASK_COLUMNS.map((column, index) => (
          <BoardColumn
            key={column}
            column={column}
            index={index}
            tasks={grouped[column]}
            dropState={dropStateFor(active, column, role)}
            note={
              role === "user" && MAINTAINER_TARGETS.includes(column) ? "Maintainer only" : undefined
            }
          />
        ))}
      </div>
      {/* A copy follows the pointer (and the keyboard); the original stays as a placeholder. */}
      <DragOverlay dropAnimation={dropAnimation} className={styles.overlay}>
        {active ? <DragPreview task={active} reducedMotion={reducedMotion} /> : null}
      </DragOverlay>
    </DndContext>
  );
}
