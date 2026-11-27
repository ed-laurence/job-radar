"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { useAnalytics } from "@/lib/AnalyticsProvider";
import type { Schemas } from "@/lib/api";
import { describeProblem } from "@/lib/api";
import type { Segment } from "@/lib/sse";
import { roleKeys } from "./api";
import type { Role } from "./labels";
import styles from "./Roles.module.css";
import { SavedKit } from "./SavedKit";
import { StreamedSegments } from "./StreamedSegments";
import { useGenerationStream } from "./useGenerationStream";

type KitPart = NonNullable<Schemas["KitRequest"]["parts"]>[number];

const PART_LABELS: Record<KitPart, string> = {
  cv: "CV",
  cover_note: "Cover note",
  outreach: "Outreach",
  answers: "Answers",
};
const PARTS = Object.keys(PART_LABELS) as KitPart[];

function partLabel(part: string | undefined): string {
  return part && part in PART_LABELS ? PART_LABELS[part as KitPart] : "Kit";
}

/** Consecutive segments of the same part, in stream order. */
function groupByPart(
  segments: Segment[],
): { key: string; part: string | undefined; segments: Segment[] }[] {
  const groups: { key: string; part: string | undefined; segments: Segment[] }[] = [];
  for (const s of segments) {
    const last = groups.at(-1);
    if (last && last.part === s.part) last.segments.push(s);
    else groups.push({ key: s.segmentId, part: s.part, segments: [s] });
  }
  return groups;
}

export interface KitPanelProps {
  role: Role;
}

export function KitPanel({ role }: KitPanelProps) {
  const id = useId();
  const queryClient = useQueryClient();
  const { track } = useAnalytics();
  const stream = useGenerationStream();
  const [parts, setParts] = useState<KitPart[]>(PARTS);
  const streaming = stream.phase === "streaming";

  const generate = async () => {
    const outcome = await stream.start(`/roles/${encodeURIComponent(role.id)}/kit`, {
      parts: PARTS.filter((p) => parts.includes(p)),
    });
    if (outcome.kind === "done") {
      track("kit_generated");
      void queryClient.invalidateQueries({ queryKey: roleKeys.detail(role.id) });
    }
  };

  const error =
    stream.phase === "error"
      ? stream.state.error?.status === 409
        ? {
            title: "A kit is already being generated for this role",
            description: "Try again when it finishes.",
            correlationId: stream.state.error.correlation_id || undefined,
          }
        : describeProblem(stream.state.error, "Kit generation failed")
      : null;

  const statusText =
    stream.phase === "streaming"
      ? "Generating…"
      : stream.phase === "done"
        ? "Kit generated."
        : stream.phase === "stopped"
          ? "Stopped. Partial output is not saved."
          : "";

  const showSaved =
    role.kit && (stream.phase === "idle" || stream.phase === "error" || stream.phase === "stopped");

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>Application kit</h2>
      <fieldset className={styles.fieldset} disabled={streaming}>
        <legend className={styles.legend}>Parts to generate</legend>
        {PARTS.map((p) => (
          <label key={p} className={styles.check}>
            <input
              type="checkbox"
              checked={parts.includes(p)}
              onChange={() =>
                setParts((cur) => (cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p]))
              }
            />
            {PART_LABELS[p]}
          </label>
        ))}
      </fieldset>
      <div className={styles.actions}>
        {streaming ? (
          <button type="button" className={styles.danger} onClick={stream.stop}>
            Stop generating
          </button>
        ) : (
          <button
            type="button"
            className={styles.primary}
            disabled={parts.length === 0}
            onClick={() => void generate()}
          >
            {role.kit ? "Regenerate kit" : "Generate kit"}
          </button>
        )}
      </div>
      <p role="status" className={styles.muted}>
        {statusText}
      </p>
      {error ? <ErrorState {...error} /> : null}
      {stream.phase !== "idle" && stream.state.segments.length > 0 ? (
        <section className={styles.output} aria-label="Generated kit" aria-busy={streaming}>
          {groupByPart(stream.state.segments).map((g) => (
            <div key={g.key}>
              <h3>{partLabel(g.part)}</h3>
              <StreamedSegments segments={g.segments} />
            </div>
          ))}
        </section>
      ) : null}
      {showSaved && role.kit ? <SavedKit kit={role.kit} roleId={role.id} /> : null}
    </section>
  );
}
