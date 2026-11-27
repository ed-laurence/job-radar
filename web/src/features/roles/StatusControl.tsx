"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { useAnalytics } from "@/lib/AnalyticsProvider";
import { describeError, isVersionConflict, type Schemas } from "@/lib/api";
import { roleKeys, useChangeStatus, useRoleWritePending } from "./api";
import {
  REJECTION_CATEGORIES,
  REJECTION_LABELS,
  type RejectionCategory,
  type Role,
  type RoleStatus,
  STATUS_LABELS,
  STATUSES,
} from "./labels";
import styles from "./Roles.module.css";

export interface StatusControlProps {
  role: Role;
}

export function StatusControl({ role }: StatusControlProps) {
  const id = useId();
  const queryClient = useQueryClient();
  const { track } = useAnalytics();
  const change = useChangeStatus(role.id);
  const writing = useRoleWritePending(role.id);
  const [status, setStatus] = useState<RoleStatus>(role.status);
  const [reasons, setReasons] = useState<RejectionCategory[]>([]);
  const [message, setMessage] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: Schemas["RoleStatusChange"] = { status, version: role.version };
    if (status === "rejected" && reasons.length) body.rejection = { reason_categories: reasons };
    setMessage("");
    change.mutate(body, {
      onSuccess: (updated) => {
        track("role_moved");
        setReasons([]);
        setMessage(`Status changed to ${STATUS_LABELS[updated.status]}`);
      },
      onError: (error) => {
        if (isVersionConflict(error)) {
          void queryClient.invalidateQueries({ queryKey: roleKeys.detail(role.id) });
        }
      },
    });
  };

  const error = change.isError
    ? isVersionConflict(change.error)
      ? {
          title: "This role changed elsewhere",
          description: "The latest version is loaded. Choose the status again.",
          correlationId: undefined,
        }
      : describeError(change.error, "Status change failed")
    : null;

  return (
    <form className={styles.panel} onSubmit={submit} aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`}>Application status</h2>
      <div className={styles.field}>
        <label htmlFor={`${id}-status`}>Status</label>
        <select
          id={`${id}-status`}
          className={styles.select}
          value={status}
          onChange={(e) => setStatus(e.target.value as RoleStatus)}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </div>
      {status === "rejected" ? (
        <fieldset className={styles.fieldset}>
          <legend className={styles.legend}>Rejection reasons</legend>
          {REJECTION_CATEGORIES.map((c) => (
            <label key={c} className={styles.check}>
              <input
                type="checkbox"
                checked={reasons.includes(c)}
                onChange={() =>
                  setReasons((r) => (r.includes(c) ? r.filter((x) => x !== c) : [...r, c]))
                }
              />
              {REJECTION_LABELS[c]}
            </label>
          ))}
        </fieldset>
      ) : null}
      <div className={styles.actions}>
        <button
          type="submit"
          className={styles.primary}
          disabled={writing || status === role.status}
        >
          Update status
        </button>
      </div>
      <p role="status" aria-label="Status update" className={styles.notice}>
        {message}
      </p>
      {error ? (
        <div role="alert" className={styles.alert}>
          <h3>{error.title}</h3>
          {error.description ? <p className={styles.muted}>{error.description}</p> : null}
          {error.correlationId ? <p>Reference: {error.correlationId}</p> : null}
        </div>
      ) : null}
    </form>
  );
}
