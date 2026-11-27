"use client";

import { useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import { useRole } from "@/lib/session";
import { useCreateTask, useMoveTask, useTasks } from "./api";
import { Board } from "./Board";
import { BoardMetrics } from "./BoardMetrics";
import { ConfirmDialog } from "./ConfirmDialog";
import { COLUMN_LABELS, type TaskColumn } from "./columns";
import styles from "./DevBoard.module.css";
import { actionErrorMessage } from "./messages";
import { moveErrorMessage, type Task } from "./model";
import { TaskFormDrawer, type TaskFormValues } from "./TaskFormDrawer";

interface PendingBlock {
  task: Task;
  rank: string;
}

export function DevBoardPage() {
  const tasks = useTasks();
  const role = useRole();
  const move = useMoveTask();
  const create = useCreateTask();
  const [creating, setCreating] = useState(false);
  const [blocking, setBlocking] = useState<PendingBlock | null>(null);
  const [notice, setNotice] = useState("");
  const [alert, setAlert] = useState("");

  const doMove = (task: Task, toColumn: TaskColumn, rank: string, reason?: string) => {
    setAlert("");
    setNotice("");
    move.mutate(
      { task, toColumn, rank, ...(reason ? { reason } : {}) },
      {
        onSuccess: (moved) =>
          setNotice(`Moved “${moved.title}” to ${COLUMN_LABELS[moved.column]}.`),
        onError: (error) => setAlert(moveErrorMessage(error, task, toColumn)),
      },
    );
  };

  const onMove = (task: Task, toColumn: TaskColumn, rank: string) => {
    if (toColumn === "blocked" && task.column !== "blocked") {
      setBlocking({ task, rank });
      return;
    }
    doMove(task, toColumn, rank);
  };

  const onCreate = (values: TaskFormValues) => {
    create.mutate(
      {
        title: values.title,
        ...(values.body ? { body: values.body } : {}),
        ...(values.labels.length ? { labels: values.labels } : {}),
      },
      {
        onSuccess: (created) => {
          setCreating(false);
          setNotice(
            created.pii_redacted
              ? `Created “${created.title}” in Backlog. Personal information was removed from the text before saving. Triage is queued.`
              : `Created “${created.title}” in Backlog. Triage is queued.`,
          );
        },
      },
    );
  };

  return (
    <section className={styles.page} aria-labelledby="page-title">
      <div className={styles.header}>
        <h1 id="page-title">Dev board</h1>
        <button
          type="button"
          className={styles.button}
          onClick={() => {
            create.reset();
            setCreating(true);
          }}
        >
          New task
        </button>
      </div>
      {role === "user" ? (
        <p className={styles.hint}>
          Moves into Plan review, Ready and Agent dev are for maintainers only.
        </p>
      ) : null}
      <div role="status" aria-live="polite" className={notice ? styles.notice : undefined}>
        {notice}
      </div>
      <div role="alert" className={alert ? styles.alert : undefined}>
        {alert}
      </div>

      <BoardMetrics />

      {tasks.isPending ? (
        <LoadingState label="Loading the board…" />
      ) : tasks.isError ? (
        <ErrorState
          title="Couldn't load the board"
          correlationId={isApiError(tasks.error) ? tasks.error.correlationId : undefined}
          onRetry={() => void tasks.refetch()}
        />
      ) : (
        <Board tasks={tasks.data} role={role} onMove={onMove} />
      )}

      {creating ? (
        <TaskFormDrawer
          mode="create"
          pending={create.isPending}
          error={create.isError ? actionErrorMessage(create.error, "create the task") : undefined}
          onSubmit={onCreate}
          onClose={() => setCreating(false)}
        />
      ) : null}
      {blocking ? (
        <ConfirmDialog
          title="Block this card"
          message={`Why is “${blocking.task.title}” blocked?`}
          confirmLabel="Move to Blocked"
          reason={{ label: "Reason", required: true, maxLength: 1000 }}
          onCancel={() => setBlocking(null)}
          onConfirm={(reason) => {
            doMove(blocking.task, "blocked", blocking.rank, reason);
            setBlocking(null);
          }}
        />
      ) : null}
    </section>
  );
}
