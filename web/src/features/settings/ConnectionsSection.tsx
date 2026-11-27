"use client";

import { type ChangeEvent, type FormEvent, useEffect, useId, useRef, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { describeError } from "@/lib/api";
import { formatDateTime } from "@/lib/settings";
import {
  type ContactsImportResult,
  useImportContacts,
  useLinkedinImport,
  useRemoveAllContacts,
} from "./api";
import { CSV_RULE, checkFile } from "./files";
import styles from "./Settings.module.css";

/** What the user types to confirm removing every contact. */
export const REMOVE_PHRASE = "remove all";

function messageOf(error: unknown, fallback: string): string {
  const e = describeError(error, fallback);
  return [e.title, e.description, ...e.fieldErrors].filter(Boolean).join(" ");
}

const n = (v: number) => v.toLocaleString("en-AU");

export function importSummary(r: ContactsImportResult): string {
  return `${n(r.imported)} imported, ${n(r.updated)} updated, ${n(r.skipped)} skipped.`;
}

export interface ConnectionsSectionProps {
  timeZone: string;
}

/** LinkedIn connections: how to export them, Import, the last import, and Remove all. */
export function ConnectionsSection({ timeZone }: ConnectionsSectionProps) {
  const id = useId();
  const summary = useLinkedinImport();
  const importer = useImportContacts();
  const removeAll = useRemoveAllContacts();
  const fileInput = useRef<HTMLInputElement>(null);
  const confirmInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [result, setResult] = useState<ContactsImportResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [note, setNote] = useState("");

  // Focus moves into the confirmation it opened.
  useEffect(() => {
    if (confirming) confirmInput.current?.focus();
  }, [confirming]);

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null;
    setResult(null);
    importer.reset();
    if (!picked) {
      setFile(null);
      setFileError(null);
      return;
    }
    const problem = checkFile(picked, CSV_RULE);
    setFileError(problem);
    setFile(problem ? null : picked);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!file) {
      setFileError(fileError ?? "Choose your Connections.csv first.");
      fileInput.current?.focus();
      return;
    }
    setNote("");
    setProgress(0);
    importer.mutate(
      { file, onProgress: setProgress },
      {
        onSuccess: (r) => {
          setResult(r);
          setFile(null);
          if (fileInput.current) fileInput.current.value = "";
        },
        onSettled: () => setProgress(null),
      },
    );
  };

  const confirmRemove = (e: FormEvent) => {
    e.preventDefault();
    if (typed.trim().toLowerCase() !== REMOVE_PHRASE) {
      setRemoveError(`Type "${REMOVE_PHRASE}" to confirm.`);
      confirmInput.current?.focus();
      return;
    }
    setRemoveError(null);
    removeAll.mutate(undefined, {
      onSuccess: (r) => {
        setConfirming(false);
        setTyped("");
        setResult(null);
        setNote(`Removed ${n(r.deleted)} contact${r.deleted === 1 ? "" : "s"}.`);
      },
      onError: (error) => setRemoveError(messageOf(error, "Your contacts couldn't be removed")),
    });
  };

  const importError = importer.isError ? messageOf(importer.error, "The import failed") : null;
  const contacts = summary.data?.contacts ?? 0;
  const last = summary.data?.last;

  return (
    <section
      id="connections"
      className={styles.section}
      aria-labelledby="connections-title"
      tabIndex={-1}
    >
      <h2 id="connections-title" className={styles.sectionTitle}>
        LinkedIn connections
      </h2>
      <p className={styles.muted}>
        Role cards show who you know at the hiring company, and kits suggest them first. Nothing is
        fetched from LinkedIn and nothing is sent to anyone.
      </p>
      <ol className={styles.steps}>
        <li>
          On LinkedIn, open{" "}
          <strong>Me → Settings &amp; Privacy → Data privacy → Get a copy of your data</strong>.
        </li>
        <li>
          Choose <strong>Connections</strong> and request the archive. LinkedIn emails a download
          link, usually within about 10 minutes.
        </li>
        <li>
          Download and unzip it, then import <code>Connections.csv</code> below. Re-import any time:
          people already here are updated, not duplicated.
        </li>
      </ol>

      {summary.isPending ? (
        <LoadingState label="Loading your last import…" />
      ) : summary.isError ? (
        <ErrorState
          {...describeError(summary.error, "Your last import failed to load")}
          onRetry={() => void summary.refetch()}
        />
      ) : (
        <p className={styles.muted}>
          {last
            ? `Last import ${formatDateTime(last.imported_at, timeZone)} (${last.filename}): ${importSummary(last)} `
            : "No import yet. "}
          You have {n(contacts)} contact{contacts === 1 ? "" : "s"}.
        </p>
      )}

      <form className={styles.subform} aria-label="Import connections" noValidate onSubmit={submit}>
        <div className={styles.field}>
          <label htmlFor={`${id}-csv`}>Connections.csv</label>
          <input
            ref={fileInput}
            id={`${id}-csv`}
            type="file"
            accept=".csv,text/csv"
            className={styles.file}
            aria-invalid={fileError ? true : undefined}
            aria-describedby={`${id}-csv-hint${fileError ? ` ${id}-csv-error` : ""}`}
            onChange={pick}
          />
          <p id={`${id}-csv-hint`} className={styles.muted}>
            CSV only, up to 2 MB.
          </p>
          {fileError ? (
            <p id={`${id}-csv-error`} className={styles.error}>
              {fileError}
            </p>
          ) : null}
        </div>
        {progress !== null ? (
          <div className={styles.field}>
            <label htmlFor={`${id}-progress`}>Importing connections</label>
            <progress id={`${id}-progress`} max={1} value={progress} />
          </div>
        ) : null}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={importer.isPending}>
            {importer.isPending ? "Importing…" : "Import contacts"}
          </button>
        </div>
        {importError ? (
          <p role="alert" className={styles.error}>
            {importError}
          </p>
        ) : null}
        <div role="status" className={styles.inlineAlert}>
          {result ? <p className={styles.ok}>Import finished: {importSummary(result)}</p> : null}
        </div>
      </form>

      <div className={styles.danger}>
        <h3 className={styles.cardTitle}>Remove all contacts</h3>
        <p className={styles.muted}>
          Deletes every imported contact, including any notes on them. This can't be undone; you can
          import Connections.csv again afterwards.
        </p>
        {confirming ? (
          <form
            className={styles.subform}
            aria-label="Confirm removing all contacts"
            noValidate
            onSubmit={confirmRemove}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setConfirming(false);
                setTyped("");
                setRemoveError(null);
              }
            }}
          >
            <div className={styles.field}>
              <label htmlFor={`${id}-confirm`}>Type "{REMOVE_PHRASE}" to confirm</label>
              <input
                ref={confirmInput}
                id={`${id}-confirm`}
                className={styles.input}
                autoComplete="off"
                value={typed}
                aria-invalid={removeError ? true : undefined}
                aria-describedby={removeError ? `${id}-confirm-error` : undefined}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
            {removeError ? (
              <p role="alert" id={`${id}-confirm-error`} className={styles.error}>
                {removeError}
              </p>
            ) : null}
            <div className={styles.actions}>
              <button
                type="submit"
                className={styles.dangerButton}
                disabled={removeAll.isPending || typed.trim().toLowerCase() !== REMOVE_PHRASE}
              >
                {removeAll.isPending ? "Removing…" : `Remove all ${n(contacts)} contacts`}
              </button>
              <button
                type="button"
                className={styles.button}
                onClick={() => {
                  setConfirming(false);
                  setTyped("");
                  setRemoveError(null);
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.button}
              disabled={contacts === 0}
              onClick={() => setConfirming(true)}
            >
              Remove all contacts…
            </button>
          </div>
        )}
      </div>
      <p className={styles.visuallyHidden} role="status" aria-live="polite">
        {note}
      </p>
    </section>
  );
}
