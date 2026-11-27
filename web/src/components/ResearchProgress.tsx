"use client";

import { useRef } from "react";
import type { ErrorView } from "@/lib/api";
import type { Step } from "@/lib/sse";
import { useStickToBottom } from "@/lib/useStickToBottom";
import { ErrorState } from "./ErrorState";
import { Markdown } from "./Markdown";
import styles from "./Research.module.css";
import { StepList } from "./StepList";

export interface ResearchProgressProps {
  running: boolean;
  finished: boolean;
  startError?: ErrorView | undefined;
  streamError?: ErrorView | undefined;
  /** Null until a run starts. */
  steps: Step[] | null;
  /** Model output: rendered with the sanitizing Markdown renderer (CODE-25). */
  summary?: string | undefined;
  sources?: readonly string[] | undefined;
}

/** A research run as it happens: a live status line, its steps, then the summary and sources. */
export function ResearchProgress({
  running,
  finished,
  startError,
  streamError,
  steps,
  summary,
  sources,
}: ResearchProgressProps) {
  const windowRef = useRef<HTMLDivElement>(null);
  useStickToBottom(windowRef);
  const hasContent = Boolean(steps?.length) || Boolean(finished && summary);
  return (
    <div className={styles.progress}>
      <p role="status" className={styles.muted}>
        {running ? "Research running…" : finished ? "Research finished." : ""}
      </p>
      {startError ? <ErrorState {...startError} /> : null}
      {streamError ? <ErrorState {...streamError} /> : null}
      {/* A chat-style window: scrolls itself to the newest step while you're at the bottom.
          Browsers make scrollable containers keyboard-focusable themselves. */}
      <section
        ref={windowRef}
        className={styles.window}
        hidden={!hasContent}
        aria-label="Research conversation"
      >
        {steps ? <StepList label="Research steps" steps={steps} /> : null}
        {finished && summary ? (
          <section className={styles.output} aria-label="Research summary">
            <Markdown>{summary}</Markdown>
            {sources?.length ? (
              <>
                <h3>Sources</h3>
                <ul>
                  {sources.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </section>
        ) : null}
      </section>
    </div>
  );
}
