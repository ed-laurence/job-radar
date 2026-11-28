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
  useMoveTask,
  usePlan,
  useRuns,
  useTask,
  useTaskDiff,
  useTaskPatch,
  useTaskReview,
  useTriage,
  useUpdateTask,
} from "./api";
import { ChangesPanel } from "./ChangesPanel";
import { ConfirmDialog } from "./ConfirmDialog";
import { COLUMN_LABELS, type TaskColumn } from "./columns";
import styles from "./DevBoard.module.css";
import { patchFilename, saveTextFile } from "./download";
import { actionErrorMessage } from "./messages";
import { canEditTask, moveErrorMessage, rankAtEnd, type Task } from "./model";
import { PlanPanel } from "./PlanPanel";
import { ReviewActions } from "./ReviewActions";
import { ReviewPanel } from "./ReviewPanel";
import { RunTree } from "./RunTree";
import { NEEDS_DECISION_LABEL } from "./review";
import { TaskChat } from "./TaskChat";
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

const REVIEW_NOTICE: Partial<Record<TaskColumn, string>> = {
  done: "Marked done. The card moved to Done.",
  changes_requested:
    "Changes requested. The card was sent back to the agent with your notes, and a new dev run starts.",
};

/**
 * One card: details, triage, plan (with approval), the branch's changes and agent review (with
 * the maintainer's decision), the run tree with live steps, and the kill switch.
 */
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
  const hasBranch = Boolean(task.data?.branch);
  const diff = useTaskDiff(taskId, hasBranch);
  const review = useTaskReview(taskId, hasBranch);
  const patch = useTaskPatch(taskId);
  const move = useMoveTask();
  const [moveTarget, setMoveTarget] = useState<TaskColumn | null>(null);
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

  const decide = (toColumn: TaskColumn, reason?: string) => {
    setMoveTarget(toColumn);
    setNotice("");
    const rank = rankAtEnd(
      queryClient.getQueryData<Task[]>(devboardKeys.tasks()),
      toColumn,
      t.rank,
    );
    move.mutate(
      { task: t, toColumn, rank, ...(reason ? { reason } : {}) },
      {
        onSuccess: () => setNotice(REVIEW_NOTICE[toColumn] ?? ""),
        onError: (error) => {
          if (isApiError(error) && error.status === 409) refreshAll();
        },
      },
    );
  };

  const downloadPatch = () => {
    const branch = t.branch ?? diff.data?.branch ?? "";
    patch.mutate(undefined, {
      onSuccess: (text) => saveTextFile(text, patchFilename(branch), "text/x-patch"),
    });
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

      {t.labels?.includes(NEEDS_DECISION_LABEL) ? (
        <p className={styles.alert}>
          <strong>Needs your decision</strong>. The agent reviewer still found blockers after the
          last review round. Read the findings, then mark the card done or request changes.
        </p>
      ) : null}

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
        labels={t.labels ?? []}
        canApprove={maintainer && t.column === "plan_review"}
        approving={approve.isPending}
        error={approve.isError ? actionErrorMessage(approve.error, "approve the plan") : undefined}
        onApprove={(allowTestChanges) => {
          if (!plan.data) return;
          approve.mutate(
            { plan: plan.data, task: t, allowTestChanges },
            { onSuccess: () => setNotice("Plan and tests approved. The card moved to Ready.") },
          );
        }}
      />
      {hasBranch ? (
        <>
          <ChangesPanel
            diff={diff.data}
            loading={diff.isPending}
            error={diff.isError ? actionErrorMessage(diff.error, "load the changes") : undefined}
            downloading={patch.isPending}
            downloadError={
              patch.isError ? actionErrorMessage(patch.error, "download the patch") : undefined
            }
            onDownloadPatch={downloadPatch}
          />
          <ReviewPanel
            review={review.data}
            loading={review.isPending}
            error={review.isError ? actionErrorMessage(review.error, "load the review") : undefined}
          />
        </>
      ) : null}
      {maintainer && t.column === "human_review" ? (
        <ReviewActions
          pending={move.isPending}
          error={
            move.isError && moveTarget ? moveErrorMessage(move.error, t, moveTarget) : undefined
          }
          onMarkDone={() => decide("done")}
          onRequestChanges={(notes) => decide("changes_requested", notes)}
        />
      ) : null}
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
      {canEditTask(role) ? <TaskChat task={t} apiBase={apiBase} /> : null}

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
