"use client";

import { Suspense, useId, useRef, useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError, isVersionConflict } from "@/lib/api";
import { useSearchQuery } from "@/lib/search";
import { type Contact, useContacts, useDeleteContact } from "./api";
import { ContactForm } from "./ContactForm";
import styles from "./Contacts.module.css";
import { ContactsTable } from "./ContactsTable";
import { fullName } from "./labels";

type Mode = { kind: "list" } | { kind: "new" } | { kind: "edit"; contact: Contact };

/** What the polite live region says about the current search; empty when there is none. */
function searchStatus(query: string, count: number, more: boolean, loading: boolean): string {
  if (!query) return "";
  if (loading) return "Searching…";
  if (count === 0) return `No contacts match “${query}”`;
  const n = `${count}${more ? "+" : ""}`;
  return `${n} ${count === 1 && !more ? "contact matches" : "contacts match"} “${query}”`;
}

/** useSearchParams needs a Suspense boundary so the rest of the route can prerender. */
export function ContactsPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading contacts…" />}>
      <ContactsView />
    </Suspense>
  );
}

function ContactsView() {
  const id = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const search = useSearchQuery();
  const list = useContacts(search.query);
  const remove = useDeleteContact();
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [deleting, setDeleting] = useState<Contact | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const contacts = list.data?.pages.flatMap((p) => p.items) ?? [];
  const clearSearch = () => {
    search.clear();
    searchRef.current?.focus();
  };

  const confirmDelete = (c: Contact) =>
    remove.mutate(
      { id: c.id, version: c.version },
      {
        onSuccess: () => setDeleteError(null),
        onError: (e) =>
          setDeleteError(
            isVersionConflict(e)
              ? `${fullName(c)} changed elsewhere. The list is refreshed; delete again if you still want to.`
              : `${fullName(c)} wasn't deleted. Try again.`,
          ),
        onSettled: () => {
          setDeleting(null);
          void list.refetch();
        },
      },
    );

  return (
    <section className={styles.page} aria-labelledby="page-title">
      <div className={styles.header}>
        <h1 id="page-title">Contacts</h1>
        {mode.kind === "list" ? (
          <button type="button" className={styles.primary} onClick={() => setMode({ kind: "new" })}>
            Add contact
          </button>
        ) : null}
      </div>
      {mode.kind === "new" ? <ContactForm onDone={() => setMode({ kind: "list" })} /> : null}
      {mode.kind === "edit" ? (
        <ContactForm
          key={mode.contact.id}
          contact={mode.contact}
          onDone={() => setMode({ kind: "list" })}
        />
      ) : null}
      {deleting ? (
        <ConfirmPanel
          title={`Delete ${fullName(deleting)}?`}
          description="The contact is removed from your list."
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
      <search className={styles.search}>
        <label htmlFor={`${id}-q`}>Search contacts</label>
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
          Name, company, position or email. Esc clears.
        </p>
      </search>
      <p role="status" className="visually-hidden">
        {searchStatus(
          search.query,
          contacts.length,
          Boolean(list.hasNextPage),
          list.isPending || (list.isFetching && !list.isFetchingNextPage),
        )}
      </p>
      {list.isPending ? (
        <LoadingState label="Loading contacts…" />
      ) : list.isError ? (
        <ErrorState
          title="Contacts failed to load"
          correlationId={isApiError(list.error) ? list.error.correlationId : undefined}
          onRetry={() => void list.refetch()}
        />
      ) : contacts.length === 0 && search.query ? (
        <EmptyState
          title={`No contacts match “${search.query}”`}
          description="Check the spelling or try part of a name, company or email."
          action={
            <button type="button" className={styles.button} onClick={clearSearch}>
              Clear search
            </button>
          }
        />
      ) : contacts.length === 0 ? (
        <EmptyState title="No contacts yet" description="Add the people you might reach out to." />
      ) : (
        <>
          <ContactsTable
            contacts={contacts}
            onEdit={(contact) => setMode({ kind: "edit", contact })}
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
                {list.isFetchingNextPage ? "Loading…" : "Load more contacts"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
