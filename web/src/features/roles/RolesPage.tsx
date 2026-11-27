"use client";

import Link from "next/link";
import { useDeferredValue, useEffect, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { describeError } from "@/lib/api";
import { type AgeRange, withAgeParams } from "./age";
import { EMPTY_FILTERS, type RoleFilters, useRoleList } from "./api";
import { RoleFiltersBar } from "./RoleFiltersBar";
import styles from "./Roles.module.css";
import { RolesTable } from "./RolesTable";

/** Roles list: filters, cursor pagination ("Load more"), and the import entry point. */
export interface RolesPageProps {
  /** The age filter from the URL (`min_age_days`, `max_age_days`), so a reload keeps it. */
  initialAge?: AgeRange;
}

export function RolesPage({ initialAge }: RolesPageProps = {}) {
  const [filters, setFilters] = useState<RoleFilters>(() =>
    initialAge && (initialAge.min !== "" || initialAge.max !== "")
      ? { ...EMPTY_FILTERS, age: initialAge }
      : EMPTY_FILTERS,
  );
  const { min, max } = filters.age;
  // The age filter lives in the query string. Changing it changes the list's query key, so
  // paging starts again from the first page.
  useEffect(() => {
    const search = withAgeParams(window.location.search, { min, max });
    if (search !== window.location.search) {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${search}`);
    }
  }, [min, max]);
  // Deferred so fast typing in the search box doesn't block input on each refetch.
  const deferred = useDeferredValue(filters);
  const list = useRoleList(deferred);
  const roles = list.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = deferred !== EMPTY_FILTERS;

  return (
    <section className={styles.page} aria-labelledby="page-title">
      <div className={styles.header}>
        <h1 id="page-title">Roles</h1>
        <Link href="/roles/new" className={styles.primary}>
          Import a role
        </Link>
      </div>
      <RoleFiltersBar value={filters} onChange={setFilters} />
      {list.isPending ? (
        <LoadingState label="Loading roles…" />
      ) : list.isError ? (
        <ErrorState
          {...describeError(list.error, "Roles failed to load")}
          onRetry={() => void list.refetch()}
        />
      ) : roles.length === 0 ? (
        <EmptyState
          title={filtered ? "No matching roles" : "No roles yet"}
          description={
            filtered
              ? "Try removing a filter."
              : "Roles arrive from the feeds, or you can import one by hand."
          }
          action={
            filtered ? undefined : (
              <Link href="/roles/new" className={styles.button}>
                Import a role
              </Link>
            )
          }
        />
      ) : (
        <>
          <RolesTable roles={roles} />
          {list.hasNextPage ? (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.button}
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                {list.isFetchingNextPage ? "Loading…" : "Load more roles"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
