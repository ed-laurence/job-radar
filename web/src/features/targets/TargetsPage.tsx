"use client";

import { Suspense, useId, useRef, useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError, isVersionConflict } from "@/lib/api";
import { useSearchQuery } from "@/lib/search";
import { NO_FILTERS, type Target, type TargetFilters, useDeleteTarget, useTargetList } from "./api";
import {
  LANE_LABELS,
  LANES,
  STATUS_LABELS,
  STATUSES,
  type TargetLane,
  type TargetStatus,
} from "./labels";
import { TargetForm } from "./TargetForm";
import { TargetResearch } from "./TargetResearch";
import styles from "./Targets.module.css";
import { TargetsTable } from "./TargetsTable";

type Mode = { kind: "list" } | { kind: "new" } | { kind: "edit"; target: Target };

/** What the polite live region says about the current search; empty when there is none. */
function searchStatus(query: string, count: number, more: boolean, loading: boolean): string {
  if (!query) return "";
  if (loading) return "Searching…";
  if (count === 0) return `No employers match “${query}”`;
  const n = `${count}${more ? "+" : ""}`;
  return `${n} ${count === 1 && !more ? "employer matches" : "employers match"} “${query}”`;
}

/** useSearchParams needs a Suspense boundary so the rest of the route can prerender. */
export function TargetsPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading employers…" />}>
      <TargetsView />
    </Suspense>
  );
}

function TargetsView() {
  const id = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const search = useSearchQuery();
  const [filters, setFilters] = useState<TargetFilters>(NO_FILTERS);
  const list = useTargetList(filters, search.query);
  const remove = useDeleteTarget();
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [deleting, setDeleting] = useState<Target | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [researching, setResearching] = useState<Target | null>(null);
  const targets = list.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = filters.status !== "" || filters.lane !== "";
  const clearSearch = () => {
    search.clear();
    searchRef.current?.focus();
  };

  const confirmDelete = (t: Target) =>
    remove.mutate(
      { id: t.id, version: t.version },
      {
        onSuccess: () => {
          setDeleteError(null);
          if (researching?.id === t.id) setResearching(null);
        },
        onError: (e) =>
          setDeleteError(
            isVersionConflict(e)
              ? `${t.name} changed elsewhere. The list is refreshed; delete again if you still want to.`
              : `${t.name} wasn't deleted. Try again.`,
          ),
        onSettled: () => setDeleting(null),
      },
    );

  return (
    <section className={styles.page} aria-labelledby="page-title">
      <div className={styles.header}>
        <h1 id="page-title">Employers</h1>
        {mode.kind === "list" ? (
          <button type="button" className={styles.primary} onClick={() => setMode({ kind: "new" })}>
            Add employer
          </button>
        ) : null}
      </div>
      <search className={styles.search}>
        <label htmlFor={`${id}-q`}>Search employers</label>
        <input
          ref={searchRef}
          id={`${id}-q`}
          className={styles.input}
          type="search"
          value={search.text}
          aria-describedby={`${id}-q-hint`}
          onChange={(e) => search.change(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && search.text) {
              e.preventDefault();
              search.clear();
            }
          }}
        />
        <p id={`${id}-q-hint`} className={styles.hint}>
          Name, why, approach, caveat or notes, across every lane. Esc clears.
        </p>
      </search>
      <fieldset className={styles.filters}>
        <legend className="visually-hidden">Filter employers</legend>
        <div className={styles.field}>
          <label htmlFor={`${id}-status`}>Status</label>
          <select
            id={`${id}-status`}
            className={styles.select}
            value={filters.status}
            onChange={(e) =>
              setFilters((f) => ({ ...f, status: e.target.value as TargetStatus | "" }))
            }
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label htmlFor={`${id}-lane`}>Lane</label>
          <select
            id={`${id}-lane`}
            className={styles.select}
            value={filters.lane}
            onChange={(e) => setFilters((f) => ({ ...f, lane: e.target.value as TargetLane | "" }))}
          >
            <option value="">All lanes</option>
            {LANES.map((l) => (
              <option key={l} value={l}>
                {LANE_LABELS[l]}
              </option>
            ))}
          </select>
        </div>
      </fieldset>
      {mode.kind === "new" ? <TargetForm onDone={() => setMode({ kind: "list" })} /> : null}
      {mode.kind === "edit" ? (
        <TargetForm
          key={mode.target.id}
          target={mode.target}
          onDone={() => setMode({ kind: "list" })}
        />
      ) : null}
      {deleting ? (
        <ConfirmPanel
          title={`Delete ${deleting.name}?`}
          description="The employer is removed from your list."
          confirmLabel="Yes, delete"
          busy={remove.isPending}
          onCancel={() => setDeleting(null)}
          onConfirm={() => confirmDelete(deleting)}
        />
      ) : null}
      {deleteError ? (
        <p role="alert" className={styles.alert}>
          {deleteError}
        </p>
      ) : null}
      {researching ? (
        <TargetResearch
          key={researching.id}
          target={researching}
          onClose={() => setResearching(null)}
        />
      ) : null}
      <p role="status" className="visually-hidden">
        {searchStatus(
          search.query,
          targets.length,
          Boolean(list.hasNextPage),
          list.isPending || (list.isFetching && !list.isFetchingNextPage),
        )}
      </p>
      {list.isPending ? (
        <LoadingState label="Loading employers…" />
      ) : list.isError ? (
        <ErrorState
          title="Employers failed to load"
          correlationId={isApiError(list.error) ? list.error.correlationId : undefined}
          onRetry={() => void list.refetch()}
        />
      ) : targets.length === 0 ? (
        search.query ? (
          <EmptyState
            title={`No employers match “${search.query}”`}
            description={
              filtered
                ? "Check the spelling, or choose another status or lane."
                : "Check the spelling or try part of a name or note."
            }
            action={
              <button type="button" className={styles.button} onClick={clearSearch}>
                Clear search
              </button>
            }
          />
        ) : filtered ? (
          <EmptyState
            title="No employers match these filters"
            description="Choose another status or lane."
          />
        ) : (
          <EmptyState
            title="No employers yet"
            description="Add the companies you want to approach, then research them."
          />
        )
      ) : (
        <>
          <TargetsTable
            targets={targets}
            onEdit={(target) => setMode({ kind: "edit", target })}
            onResearch={setResearching}
            onDelete={setDeleting}
          />
          {list.hasNextPage ? (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.button}
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                {list.isFetchingNextPage ? "Loading…" : "Load more employers"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
