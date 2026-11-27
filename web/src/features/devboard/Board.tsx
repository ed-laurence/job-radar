"use client";

import {
  DndContext,
  type DragEndEvent,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useMemo, useRef, useState } from "react";
import { buildAnnouncements, SCREEN_READER_INSTRUCTIONS } from "./announcements";
import { BoardColumn } from "./BoardColumn";
import { columnFirstCollision } from "./collision";
import {
  type AppRole,
  isTargetLocked,
  MAINTAINER_TARGETS,
  TASK_COLUMNS,
  type TaskColumn,
} from "./columns";
import styles from "./DevBoard.module.css";
import { createBoardKeyboard } from "./keyboard";
import { groupByColumn, resolveDrop, type Task } from "./model";

export interface BoardProps {
  tasks: Task[];
  role: AppRole | undefined;
  onMove: (task: Task, toColumn: TaskColumn, rank: string) => void;
}

/** The Kanban board: pointer and keyboard drag (dnd-kit) with screen-reader announcements. */
export function Board({ tasks, role, onMove }: BoardProps) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const tasksRef = useRef(tasks);
  const [keyboard] = useState(createBoardKeyboard);
  tasksRef.current = tasks;
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: keyboard.coordinateGetter }),
  );
  const announcements = useMemo(
    () => buildAnnouncements(() => tasksRef.current, keyboard.target),
    [keyboard],
  );
  const grouped = useMemo(() => groupByColumn(tasks), [tasks]);
  const active = activeId ? tasks.find((t) => t.id === activeId) : undefined;

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
            locked={active ? isTargetLocked(active, column, role) : false}
            note={
              role === "user" && MAINTAINER_TARGETS.includes(column) ? "Maintainer only" : undefined
            }
          />
        ))}
      </div>
    </DndContext>
  );
}
