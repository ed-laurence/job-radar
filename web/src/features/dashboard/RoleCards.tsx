"use client";

import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError, isVersionConflict, type Schemas } from "@/lib/api";
import { type Track, useSetStatus, useTabRoles } from "./api";
import { ACTION_DONE, type CardAction } from "./card";
import styles from "./Dashboard.module.css";
import { RoleCard } from "./RoleCard";
import { TABS, type Tab } from "./tabs";
import { useArrivals } from "./useArrivals";

export interface RoleCardsProps {
  tab: Tab;
  track?: Track;
  /** Shared with the action row, so archive results are announced in the same place. */
  notice: string;
  alert: string;
  onNotice: (text: string) => void;
  onAlert: (text: string) => void;
}

/** The role cards for the chosen status tab, with Show more and status changes. */
export function RoleCards({
  tab,
  track,
  notice,
  alert,
  onNotice: setNotice,
  onAlert: setAlert,
}: RoleCardsProps) {
  const list = useTabRoles(tab, track);
  const setStatus = useSetStatus();
  const roles = list.data?.pages.flatMap((p) => p.items) ?? [];
  const arrivals = useArrivals(
    `${tab}|${track ?? "all"}`,
    list.data?.pages[0]?.items.map((r) => r.id),
    roles.map((r) => r.id),
  );
  const now = Date.now();

  const onAction = (role: Schemas["Role"], action: CardAction) => {
    setNotice("");
    setAlert("");
    setStatus.mutate(
      { role, status: action },
      {
        onSuccess: (r) => setNotice(`“${r.title}” ${ACTION_DONE[action]}.`),
        onError: (e) =>
          setAlert(
            isVersionConflict(e)
              ? `“${role.title}” changed elsewhere. The list has been refreshed; try again.`
              : `Couldn't update “${role.title}”.${isApiError(e) && e.correlationId ? ` Reference: ${e.correlationId}` : ""}`,
          ),
      },
    );
  };

  return (
    <section className={styles.list} aria-label={`${TABS[tab].label} roles`}>
      <p role="status" className={styles.notice}>
        {notice}
      </p>
      <p aria-live="polite" className="visually-hidden">
        {arrivals.note}
      </p>
      {alert ? (
        <p role="alert" className={styles.alert}>
          {alert}
        </p>
      ) : null}
      {list.isPending ? (
        <LoadingState label="Loading roles…" />
      ) : list.isError ? (
        <ErrorState
          title="Roles failed to load"
          correlationId={isApiError(list.error) ? list.error.correlationId : undefined}
          onRetry={() => void list.refetch()}
        />
      ) : roles.length === 0 ? (
        <EmptyState
          title={`No ${TABS[tab].label.toLowerCase()} roles`}
          description={TABS[tab].empty}
        />
      ) : (
        <>
          {roles.map((r) => (
            <RoleCard
              key={r.id}
              role={r}
              now={now}
              arrived={arrivals.ids.has(r.id)}
              busy={setStatus.isPending && setStatus.variables?.role.id === r.id}
              onAction={onAction}
            />
          ))}
          {list.hasNextPage ? (
            <button
              type="button"
              className={styles.button}
              disabled={list.isFetchingNextPage}
              onClick={() => void list.fetchNextPage()}
            >
              {list.isFetchingNextPage ? "Loading…" : "Show more"}
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}
