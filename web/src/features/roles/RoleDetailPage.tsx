"use client";

import Link from "next/link";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { describeError, isNotFound } from "@/lib/api";
import { useRole } from "./api";
import { ChatPanel } from "./ChatPanel";
import { DeleteRole } from "./DeleteRole";
import { EvidencePanel } from "./EvidencePanel";
import { KitPanel } from "./KitPanel";
import { ResearchPanel } from "./ResearchPanel";
import { RoleEditor } from "./RoleEditor";
import { RoleOverview } from "./RoleOverview";
import styles from "./Roles.module.css";
import { StatusControl } from "./StatusControl";

export interface RoleDetailPageProps {
  roleId: string;
}

export function RoleDetailPage({ roleId }: RoleDetailPageProps) {
  const role = useRole(roleId);

  if (role.isPending) return <LoadingState label="Loading role…" />;
  if (role.isError) {
    return (
      <section className={styles.page} aria-label="Role">
        {isNotFound(role.error) ? (
          <ErrorState
            title="Role not found"
            description="It may have been deleted, or it isn't visible to you."
          />
        ) : (
          <ErrorState
            {...describeError(role.error, "Role failed to load")}
            onRetry={() => void role.refetch()}
          />
        )}
        <Link href="/roles">Back to roles</Link>
      </section>
    );
  }

  const data = role.data;
  return (
    <article className={styles.page} aria-labelledby="page-title">
      <Link href="/roles">Back to roles</Link>
      <RoleOverview role={data} />
      <EvidencePanel key={`evidence-${data.id}`} role={data} />
      <div className={styles.actions}>
        <RoleEditor key={`edit-${data.id}`} role={data} />
        <DeleteRole role={data} />
      </div>
      <StatusControl role={data} />
      <div className={styles.grid}>
        {/* Anchors for the dashboard's Write kit and Chat links. */}
        <div id="kit">
          <KitPanel role={data} />
        </div>
        <div id="chat">
          <ChatPanel roleId={data.id} />
        </div>
      </div>
      <ResearchPanel company={data.company} />
    </article>
  );
}
