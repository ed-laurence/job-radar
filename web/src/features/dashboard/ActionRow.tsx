"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { isApiError } from "@/lib/api";
import { useUserSettings } from "@/lib/settings";
import { type Track, useArchiveOlder } from "./api";
import styles from "./Dashboard.module.css";

export interface ActionRowProps {
  track: Track | undefined;
  onTrack: (track: Track | undefined) => void;
  /** Archive is only offered on Act now, as on job-seeker. */
  showArchive: boolean;
  onNotice: (text: string) => void;
  onAlert: (text: string) => void;
}

const TRACKS: readonly { value: Track; label: string }[] = [
  { value: "contract", label: "Contract" },
  { value: "exec", label: "Exec / leadership" },
  { value: "mission", label: "Mission-driven" },
];

/** + Log an application, Archive older than N days, and the track filter. */
export function ActionRow({ track, onTrack, showArchive, onNotice, onAlert }: ActionRowProps) {
  const id = useId();
  const settings = useUserSettings();
  // Until the user types a value, the input follows the saved default (Settings).
  const [typed, setDays] = useState<number | undefined>(undefined);
  const days = typed ?? settings.data?.archive_default_days ?? 14;
  const archive = useArchiveOlder();
  const valid = Number.isInteger(days) && days >= 1 && days <= 365;

  const onArchive = () => {
    onNotice("");
    onAlert("");
    archive.mutate(days, {
      onSuccess: (r) =>
        onNotice(
          r.archived
            ? `Archived ${r.archived} role${r.archived === 1 ? "" : "s"} posted more than ${days} days ago.`
            : `No Act now roles are older than ${days} days.`,
        ),
      onError: (e) =>
        onAlert(
          `Couldn't archive older roles.${isApiError(e) && e.correlationId ? ` Reference: ${e.correlationId}` : ""}`,
        ),
    });
  };

  return (
    <>
      <Link className={styles.primary} href="/roles/new">
        + Log an application
      </Link>
      {showArchive ? (
        <span className={styles.archive}>
          <button
            type="button"
            className={styles.button}
            disabled={!valid || archive.isPending}
            title="Move Act now roles posted more than this many days ago to Passed (as closed)"
            aria-describedby={`${id}-days`}
            onClick={onArchive}
          >
            Archive older than
          </button>
          <input
            id={`${id}-days`}
            type="number"
            min={1}
            max={365}
            value={Number.isNaN(days) ? "" : days}
            aria-label="Days"
            onChange={(e) => setDays(e.target.valueAsNumber)}
          />
          <span aria-hidden="true">days</span>
        </span>
      ) : null}
      <select
        className={styles.select}
        aria-label="Track"
        value={track ?? ""}
        onChange={(e) => onTrack((e.target.value || undefined) as Track | undefined)}
      >
        <option value="">All tracks</option>
        {TRACKS.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </select>
    </>
  );
}
