"use client";

import { useId, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { ResearchForm } from "@/components/ResearchForm";
import { ResearchProgress } from "@/components/ResearchProgress";
import { describeError, describeProblem } from "@/lib/api";
import { type ResearchFocus, useResearch } from "@/lib/research";
import { useTargets } from "./api";
import styles from "./Roles.module.css";

export interface ResearchPanelProps {
  /** Used to preselect the campaign target with the same name. */
  company: string | undefined;
}

/** Starts the company-research agent on a campaign target and shows its steps live. */
export function ResearchPanel({ company }: ResearchPanelProps) {
  const id = useId();
  const targets = useTargets();
  const research = useResearch();
  const [chosen, setChosen] = useState<string | null>(null);
  const [focus, setFocus] = useState<ResearchFocus[]>(["overview"]);

  const items = targets.data?.items ?? [];
  const match = items.find((t) => t.name.trim().toLowerCase() === company?.trim().toLowerCase());
  const targetId = chosen ?? match?.id ?? items[0]?.id ?? "";

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>Company research</h2>
      {targets.isPending ? (
        <LoadingState label="Loading employers…" />
      ) : targets.isError ? (
        <ErrorState
          {...describeError(targets.error, "Employers failed to load")}
          onRetry={() => void targets.refetch()}
        />
      ) : items.length === 0 ? (
        <p className={styles.muted}>
          No employers yet. Research runs on an employer, so add the company on the Employers page
          first.
        </p>
      ) : (
        <ResearchForm
          targets={items}
          targetId={targetId}
          onTargetChange={setChosen}
          focus={focus}
          onFocusChange={setFocus}
          running={research.running}
          onStart={(ordered) => {
            if (targetId) research.start(targetId, ordered);
          }}
        />
      )}
      <ResearchProgress
        running={research.running}
        finished={research.finished}
        startError={
          research.startError
            ? describeError(research.startError, "Research could not start")
            : undefined
        }
        streamError={
          research.failed ? describeProblem(research.streamError, "Research failed") : undefined
        }
        steps={research.steps}
        summary={research.run?.summary}
        sources={research.run?.sources}
      />
    </section>
  );
}
