"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ResearchForm } from "@/components/ResearchForm";
import { ResearchProgress } from "@/components/ResearchProgress";
import { describeError, describeProblem } from "@/lib/api";
import { type ResearchFocus, useResearch } from "@/lib/research";
import type { Target } from "./api";
import styles from "./Targets.module.css";

export interface TargetResearchProps {
  target: Target;
  onClose: () => void;
}

/** Runs the company-research agent on one target and shows its steps live. */
export function TargetResearch({ target, onClose }: TargetResearchProps) {
  const id = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const research = useResearch();
  const [focus, setFocus] = useState<ResearchFocus[]>(["overview"]);

  // Opened from a row button: move focus here so keyboard and screen reader users follow.
  useEffect(() => headingRef.current?.focus(), []);

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <div className={styles.header}>
        <h2 id={`${id}-title`} ref={headingRef} tabIndex={-1}>
          Research: {target.name}
        </h2>
        <button type="button" className={styles.button} onClick={onClose}>
          Close research
        </button>
      </div>
      <ResearchForm
        focus={focus}
        onFocusChange={setFocus}
        running={research.running}
        onStart={(ordered) => research.start(target.id, ordered)}
      />
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
