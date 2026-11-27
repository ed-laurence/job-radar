"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import { describeError, isVersionConflict } from "@/lib/api";
import { useDeleteRole } from "./api";
import type { Role } from "./labels";
import styles from "./Roles.module.css";

export interface DeleteRoleProps {
  role: Role;
}

/** Soft delete (the row is kept for dedupe but hidden), behind an explicit confirmation. */
export function DeleteRole({ role }: DeleteRoleProps) {
  const router = useRouter();
  const remove = useDeleteRole(role.id);
  const [confirming, setConfirming] = useState(false);

  const error = remove.isError
    ? isVersionConflict(remove.error)
      ? "This role changed elsewhere. Review the latest version, then delete again."
      : describeError(remove.error, "Delete failed").title
    : null;

  return (
    <div className={styles.actions}>
      {confirming ? (
        <ConfirmPanel
          title="Delete this role?"
          description="It disappears from your lists and pipeline. The feed won't import it again."
          confirmLabel="Yes, delete"
          busy={remove.isPending}
          onCancel={() => setConfirming(false)}
          onConfirm={() =>
            remove.mutate(role.version, {
              onSuccess: () => router.push("/roles"),
              onSettled: () => setConfirming(false),
            })
          }
        />
      ) : (
        <button type="button" className={styles.danger} onClick={() => setConfirming(true)}>
          Delete role
        </button>
      )}
      {error ? (
        <p role="alert" className={styles.alert}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
