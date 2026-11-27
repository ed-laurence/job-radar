"use client";

import { type ChangeEvent, useId, useRef, useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import type { Schemas } from "@/lib/api";
import { formatDateTime } from "@/lib/settings";
import { checkFile, formatBytes, PDF_RULE } from "./files";
import styles from "./Settings.module.css";

export type DocumentReadability = Schemas["Readability"];

export interface DocumentInfo {
  filename: string;
  uploadedAt: string;
  pages: number;
  sizeBytes: number;
  readability: DocumentReadability;
  archivedVersions: number;
}

export interface DocumentCardProps {
  title: string;
  hint: string;
  doc: DocumentInfo | null;
  timeZone: string;
  /** 0..1 while a file is being read and sent; null when idle. */
  progress: number | null;
  /** The last upload, view or remove failure, already worded for the user. */
  error: string | null;
  removing: boolean;
  onUpload: (file: File) => void;
  onView: () => void;
  onRemove: () => void;
  /** Shows a client-side check failure (or clears it with null). */
  onClientError: (message: string | null) => void;
}

/** "✓ Readable: …" when scoring and kits can use the text, otherwise what's missing. */
export function readabilityText(r: DocumentReadability): { ok: boolean; text: string } {
  const counts = `${r.sections} sections, ${r.roles} roles and ${r.bullets} bullets found`;
  const missing = [
    r.sections ? "" : "sections",
    r.roles ? "" : "roles",
    r.bullets ? "" : "bullets",
  ].filter(Boolean);
  if (r.ok && !missing.length) return { ok: true, text: `✓ Readable: ${counts}` };
  if (missing.length) {
    return {
      ok: false,
      text: `Hard to read: no ${missing.join(", ")} found. Scoring and kits may miss parts of it.`,
    };
  }
  return { ok: false, text: `Hard to read (${counts}). Scoring and kits may miss parts of it.` };
}

/** One upload slot: what's stored, a readability check, and View, Replace or Upload, Remove. */
export function DocumentCard({
  title,
  hint,
  doc,
  timeZone,
  progress,
  error,
  removing,
  onUpload,
  onView,
  onRemove,
  onClientError,
}: DocumentCardProps) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  const uploading = progress !== null;

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const problem = checkFile(file, PDF_RULE);
    onClientError(problem);
    if (!problem) onUpload(file);
  };

  const check = doc ? readabilityText(doc.readability) : null;

  return (
    <article className={styles.card} aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} className={styles.cardTitle}>
        {title}
      </h3>
      <p className={styles.muted}>{hint}</p>
      {doc ? (
        <>
          <p className={styles.fileName}>{doc.filename}</p>
          <p className={styles.muted}>
            Uploaded {formatDateTime(doc.uploadedAt, timeZone)} · {doc.pages} page
            {doc.pages === 1 ? "" : "s"} · {formatBytes(doc.sizeBytes)}
          </p>
          {check ? <p className={check.ok ? styles.ok : styles.warn}>{check.text}</p> : null}
          {doc.archivedVersions ? (
            <p className={styles.muted}>
              {doc.archivedVersions} earlier version{doc.archivedVersions === 1 ? " is" : "s are"}{" "}
              kept.
            </p>
          ) : null}
        </>
      ) : (
        <p className={styles.muted}>Not uploaded.</p>
      )}

      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        className={styles.visuallyHidden}
        tabIndex={-1}
        aria-hidden="true"
        data-testid={`file-${title}`}
        onChange={pick}
      />
      <div className={styles.actions}>
        {doc ? (
          <button type="button" className={styles.button} onClick={onView}>
            View<span className={styles.visuallyHidden}> {title} (opens in a new tab)</span>
          </button>
        ) : null}
        <button
          type="button"
          className={doc ? styles.button : styles.primary}
          disabled={uploading || removing}
          aria-describedby={`${id}-limits`}
          onClick={() => input.current?.click()}
        >
          {doc ? "Replace…" : "Upload…"}
          <span className={styles.visuallyHidden}> {title}</span>
        </button>
        {doc ? (
          <button
            ref={removeButton}
            type="button"
            className={styles.button}
            disabled={uploading || removing}
            onClick={() => setConfirming(true)}
          >
            {removing ? "Removing…" : "Remove"}
            <span className={styles.visuallyHidden}> {title}</span>
          </button>
        ) : null}
      </div>
      <p id={`${id}-limits`} className={styles.muted}>
        PDF only, up to 5 MB.
      </p>
      {uploading ? (
        <div className={styles.field}>
          <label htmlFor={`${id}-progress`}>Uploading {title}</label>
          <progress id={`${id}-progress`} max={1} value={progress} />
        </div>
      ) : null}
      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      {confirming ? (
        <ConfirmPanel
          title={`Remove your ${title}?`}
          description="Scoring and kits stop using it straight away. Earlier versions are kept, and you can upload it again later."
          confirmLabel="Remove"
          busy={removing}
          onConfirm={() => {
            setConfirming(false);
            onRemove();
          }}
          onCancel={() => {
            setConfirming(false);
            removeButton.current?.focus();
          }}
        />
      ) : null}
    </article>
  );
}
