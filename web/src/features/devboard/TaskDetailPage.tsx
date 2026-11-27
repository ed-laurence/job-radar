"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import { useRole } from "@/lib/session";
import {
  devboardKeys,
  useApprovePlan,
  useDeleteTask,
  useKillTask,
  usePlan,
  useRuns,
  useTask,
  useTriage,
  useUpdateTask,
} from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { COLUMN_LABELS } from "./columns";
import styles from "./DevBoard.module.css";
import { actionErrorMessage } from "./messages";
import { PlanPanel } from "./PlanPanel";
import { RunTree } from "./RunTree";
import { TaskFormDrawer, type TaskFormValues } from "./TaskFormDrawer";
import { TriagePanel } from "./TriagePanel";

export interface TaskDetailPageProps {
  taskId: string;
  /** API base for the step streams; the app default is `/api`. */
  apiBase?: string;
}

type Dialog = "edit" | "delete" | "kill" | null;

function sameLabels(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((l, i) => l === b[i]);
}

/** One card: details, triage, plan (with approval), the run tree with live steps, and the kill switch. */
export function TaskDetailPage({ taskId, apiBase }: TaskDetailPageProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const role = useRole();
  const task = useTask(taskId);
  const triage = useTriage(taskId);
  const plan = usePlan(taskId);
  const runs = useRuns(taskId);
  const update = useUpdateTask(taskId);
  const remove = useDeleteTask(taskId);
  const approve = useApprovePlan(taskId);
  const kill = useKillTask(taskId);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState("");

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) });
    void queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() });
  };

  if (task.isPending) return <LoadingState label="Loading the task…" />;
  if (task.isError) {
    const notFound = isApiError(task.error) && task.error.status === 404;
    return (
      <ErrorState
        title={notFound ? "Task not found" : "Couldn't load the task"}
        correlationId={isApiError(task.error) ? task.error.correlationId : undefined}
        {...(notFound ? {} : { onRetry: () => void task.refetch() })}
      />
    );
  }

  const t = task.data;
  const maintainer = role === "maintainer";
  const open = (d: Dialog) => {
    update.reset();
    remove.reset();
    kill.reset();
    setDialog(d);
  };

  const onSave = (values: TaskFormValues) => {
    const changes = {
      ...(values.title !== t.title ? { title: values.title } : {}),
      ...(values.body !== (t.body ?? "") ? { body: values.body } : {}),
      ...(!sameLabels(values.labels, t.labels ?? []) ? { labels: values.labels } : {}),
    };
    if (Object.keys(changes).length === 0) {
      setDialog(null);
      return;
    }
    update.mutate(
      { version: t.version, ...changes },
      {
        onSuccess: (saved) => {
          setDialog(null);
          setNotice(
            saved.pii_redacted
              ? "Saved. Personal information was removed from the text before saving. Triage runs again."
              : "Saved. Triage runs again.",
          );
        },
      },
    );
  };

  return (
    <article className={styles.page} aria-labelledby="page-title">
      <p>
        <Link href="/dev-board">← Back to the board</Link>
      </p>
      <div className={styles.header}>
        <h1 id="page-title">{t.title}</h1>
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={() => open("edit")}>
            Edit
          </button>
          <button type="button" className={styles.secondary} onClick={() => open("delete")}>
            Delete
          </button>
          {maintainer && t.active_run_id ? (
            <button type="button" className={styles.danger} onClick={() => open("kill")}>
              Kill run
            </button>
          ) : null}
        </div>
      </div>
      <div role="status" aria-live="polite" className={notice ? styles.notice : undefined}>
        {notice}
      </div>

      <section className={styles.section} aria-labelledby="details-title">
        <h2 id="details-title">Details</h2>
        <dl className={styles.facts}>
          <dt>Column</dt>
          <dd>{COLUMN_LABELS[t.column]}</dd>
          {t.labels?.length ? (
            <>
              <dt>Labels</dt>
              <dd>{t.labels.join(", ")}</dd>
            </>
          ) : null}
          {t.blocked_reason ? (
            <>
              <dt>Blocked</dt>
              <dd>{t.blocked_reason}</dd>
            </>
          ) : null}
          {t.pr_url ? (
            <>
              <dt>Pull request</dt>
              <dd>
                <a href={t.pr_url} rel="noopener noreferrer" target="_blank">
                  Open the pull request
                </a>
              </dd>
            </>
          ) : null}
        </dl>
        {t.pii_redacted ? (
          <p className={styles.hint}>
            Personal information was removed from this task when it was saved.
          </p>
        ) : null}
        {t.body ? (
          <p className={styles.body}>{t.body}</p>
        ) : (
          <p className={styles.hint}>No description.</p>
        )}
      </section>

      <TriagePanel triage={triage.data} loading={triage.isPending} />
      <PlanPanel
        plan={plan.data}
        loading={plan.isPending}
        canApprove={maintainer && t.column === "plan_review"}
        approving={approve.isPending}
        error={approve.isError ? actionErrorMessage(approve.error, "approve the plan") : undefined}
        onApprove={() => {
          if (!plan.data) return;
          approve.mutate(
            { plan: plan.data, task: t },
            { onSuccess: () => setNotice("Plan and tests approved. The card moved to Ready.") },
          );
        }}
      />
      <RunTree
        runs={runs.data}
        plan={plan.data}
        loading={runs.isPending}
        apiBase={apiBase}
        onRunFinished={() => {
          void queryClient.invalidateQueries({ queryKey: devboardKeys.runs(taskId) });
          void queryClient.invalidateQueries({ queryKey: devboardKeys.triage(taskId) });
          void queryClient.invalidateQueries({ queryKey: devboardKeys.plan(taskId) });
          refreshAll();
        }}
      />

      {dialog === "edit" ? (
        <TaskFormDrawer
          mode="edit"
          initial={{ title: t.title, body: t.body ?? "", labels: t.labels ?? [] }}
          pending={update.isPending}
          error={update.isError ? actionErrorMessage(update.error, "save the task") : undefined}
          onSubmit={onSave}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === "delete" ? (
        <ConfirmDialog
          title="Delete this task?"
          message={`“${t.title}” is removed from the board.`}
          confirmLabel="Delete task"
          danger
          pending={remove.isPending}
          error={remove.isError ? actionErrorMessage(remove.error, "delete the task") : undefined}
          onCancel={() => setDialog(null)}
          onConfirm={() => remove.mutate(t.version, { onSuccess: () => router.push("/dev-board") })}
        />
      ) : null}
      {dialog === "kill" ? (
        <ConfirmDialog
          title="Kill the active run?"
          message="This stops the run and all its workers, releases their leases and moves the card to Blocked."
          confirmLabel="Kill run"
          danger
          reason={{ label: "Reason (optional)", required: false, maxLength: 500 }}
          pending={kill.isPending}
          error={kill.isError ? actionErrorMessage(kill.error, "kill the run") : undefined}
          onCancel={() => setDialog(null)}
          onConfirm={(reason) =>
            kill.mutate(
              { version: t.version, ...(reason ? { reason } : {}) },
              {
                onSuccess: () => {
                  setDialog(null);
                  setNotice("Run killed. The card moved to Blocked.");
                },
              },
            )
          }
        />
      ) : null}
    </article>
  );
}
