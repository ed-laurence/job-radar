"use client";

import { useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { describeError } from "@/lib/api";
import {
  type DocumentSlot,
  type ProfileDocument,
  useDocumentFileUrl,
  useProfileDocuments,
  useRemoveDocument,
  useUploadDocument,
} from "./api";
import { DocumentCard, type DocumentInfo } from "./DocumentCard";
import styles from "./Settings.module.css";

const SLOTS: readonly { slot: DocumentSlot; title: string; hint: string }[] = [
  {
    slot: "technical",
    title: "Technical CV",
    hint: "Used for engineering and hands-on roles.",
  },
  {
    slot: "leadership",
    title: "Leadership CV",
    hint: "Used for lead, manager and head-of roles.",
  },
  {
    slot: "linkedin_profile",
    title: "LinkedIn profile PDF",
    hint: "On your LinkedIn profile choose More (or Resources), then Save to PDF. Kits are checked against it.",
  },
];

/** How long an opened PDF's object URL lives; the new tab has loaded it well before then. */
const REVOKE_AFTER_MS = 60_000;

function toInfo(d: ProfileDocument): DocumentInfo {
  return {
    filename: d.filename,
    uploadedAt: d.uploaded_at,
    pages: d.pages,
    sizeBytes: d.size_bytes,
    readability: d.readability,
    archivedVersions: d.archived_versions,
  };
}

function messageOf(error: unknown, fallback: string): string {
  const e = describeError(error, fallback);
  return [e.title, e.description, ...e.fieldErrors].filter(Boolean).join(" ");
}

export interface DocumentsSectionProps {
  timeZone: string;
}

/** CVs & LinkedIn profile: one card per slot. */
export function DocumentsSection({ timeZone }: DocumentsSectionProps) {
  const docs = useProfileDocuments();
  const upload = useUploadDocument();
  const remove = useRemoveDocument();
  const fileUrl = useDocumentFileUrl();
  const [progress, setProgress] = useState<Partial<Record<DocumentSlot, number>>>({});
  const [errors, setErrors] = useState<Partial<Record<DocumentSlot, string | null>>>({});
  const [removing, setRemoving] = useState<DocumentSlot | null>(null);
  const [note, setNote] = useState("");

  const setError = (slot: DocumentSlot, message: string | null) =>
    setErrors((e) => ({ ...e, [slot]: message }));
  const setSlotProgress = (slot: DocumentSlot, value: number | undefined) =>
    setProgress((p) => {
      const next = { ...p };
      if (value === undefined) delete next[slot];
      else next[slot] = value;
      return next;
    });

  const onUpload = (slot: DocumentSlot, title: string, file: File) => {
    setError(slot, null);
    setNote("");
    setSlotProgress(slot, 0);
    upload.mutate(
      { slot, file, onProgress: (f) => setSlotProgress(slot, f) },
      {
        onSuccess: () => setNote(`${title} uploaded.`),
        onError: (error) => setError(slot, messageOf(error, `${title} upload failed`)),
        onSettled: () => setSlotProgress(slot, undefined),
      },
    );
  };

  const onRemove = (slot: DocumentSlot, title: string) => {
    setError(slot, null);
    setNote("");
    setRemoving(slot);
    remove.mutate(slot, {
      onSuccess: () => setNote(`${title} removed.`),
      onError: (error) => setError(slot, messageOf(error, `${title} couldn't be removed`)),
      onSettled: () => setRemoving(null),
    });
  };

  const onView = async (slot: DocumentSlot, title: string) => {
    setError(slot, null);
    // Opened now, inside the click, so pop-up blockers allow it; the PDF arrives a moment later.
    const tab = window.open("", "_blank");
    try {
      const url = await fileUrl(slot);
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      } else {
        window.location.assign(url);
      }
      window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
    } catch (error) {
      tab?.close();
      setError(slot, messageOf(error, `${title} couldn't be opened`));
    }
  };

  const bySlot = new Map((docs.data?.items ?? []).map((d) => [d.slot, d]));

  return (
    <section id="cvs" className={styles.section} aria-labelledby="cvs-title" tabIndex={-1}>
      <h2 id="cvs-title" className={styles.sectionTitle}>
        CVs &amp; LinkedIn profile
      </h2>
      <p className={styles.muted}>
        Scoring reads your CV text to judge each role, and kits pick the better CV and tailor it.
        Replacing a file keeps the earlier version. Files are PDFs of up to 5 MB.
      </p>
      {docs.isPending ? (
        <LoadingState label="Loading your documents…" />
      ) : docs.isError ? (
        <ErrorState
          {...describeError(docs.error, "Your documents failed to load")}
          onRetry={() => void docs.refetch()}
        />
      ) : (
        <div className={styles.cards}>
          {SLOTS.map(({ slot, title, hint }) => {
            const doc = bySlot.get(slot);
            return (
              <DocumentCard
                key={slot}
                title={title}
                hint={hint}
                doc={doc ? toInfo(doc) : null}
                timeZone={timeZone}
                progress={progress[slot] ?? null}
                error={errors[slot] ?? null}
                removing={removing === slot}
                onUpload={(file) => onUpload(slot, title, file)}
                onView={() => void onView(slot, title)}
                onRemove={() => onRemove(slot, title)}
                onClientError={(m) => setError(slot, m)}
              />
            );
          })}
        </div>
      )}
      <p className={styles.visuallyHidden} role="status" aria-live="polite">
        {note}
      </p>
    </section>
  );
}
