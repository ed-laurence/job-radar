"use client";

import { useId } from "react";
import { type ErrorView, labelFor, type Schemas } from "@/lib/api";
import type { EvidenceController, EvidenceTest, EvidenceTestItem } from "@/lib/evidence";
import { formatShortDateTime } from "@/lib/settings";
import { Dialog } from "./Dialog";
import styles from "./EvidenceBlock.module.css";
import { EMPTY_EXPERIENCE, ExperienceForm } from "./ExperienceForm";

type Summary = Schemas["RoleEvidence"];
type Verdict = Schemas["EvidenceVerdict"];
type Match = Schemas["EvidenceMatch"];

const VERDICT_CLASS: Readonly<Record<Verdict, string | undefined>> = {
  apply: styles.apply,
  consider: styles.consider,
  skip: styles.skip,
};

/** Used until the full test (with its own verdict text) has loaded. */
const VERDICT_HELP: Readonly<Record<Verdict, string>> = {
  apply: "Passes the evidence test: apply.",
  consider: "Don't cold-apply: go through a recruiter, a referral or a note to the hiring manager.",
  skip: "Fails the evidence test: pass, unless you can get a conversation there.",
};

const MATCH: Readonly<
  Record<Match, { mark: string; label: string; className: string | undefined }>
> = {
  shown: { mark: "✓", label: "shown", className: styles.matched },
  partial: { mark: "≈", label: "partly shown", className: styles.adjacent },
  missing: { mark: "✗", label: "missing", className: styles.missing },
};

const SOURCE: Readonly<Record<Schemas["EvidenceTestSource"], string>> = {
  cv_technical: "technical CV",
  cv_leadership: "leadership CV",
  records: "your records · not on CV yet",
  none: "",
};

export interface EvidenceBlockProps extends EvidenceController {
  /** A collapsed, expandable strip on a role card, or the always-open block on role detail. */
  variant: "card" | "detail";
  /** The role's evidence summary, shown until the full test loads. */
  summary?: Summary | undefined;
  /** Card only: the strip was opened or closed (load the full test on open). */
  onToggle?: (open: boolean) => void;
}

/** "Apply · you show 4 of 5 must-haves (1 from your records, not on your CV)". */
export function evidenceHeadline(ev: Summary | EvidenceTest): { verdict: string; detail: string } {
  const total = "total" in ev ? ev.total : ev.must_haves_total;
  const records = ev.from_records ? ` (${ev.from_records} from your records, not on your CV)` : "";
  return {
    verdict: labelFor("EvidenceVerdict", ev.verdict),
    detail: `you show ${ev.shown} of ${total} must-haves${records}`,
  };
}

function InlineError({
  view,
  onRetry,
  announce = true,
}: {
  view: ErrorView;
  onRetry?: () => void;
  /** False when the container already announces it (the form's own alert region). */
  announce?: boolean;
}) {
  return (
    <div role={announce ? "alert" : undefined} className={styles.alert}>
      <p className={styles.alertTitle}>{view.title}</p>
      {[view.description, ...view.fieldErrors].filter(Boolean).map((m) => (
        <p key={m}>{m}</p>
      ))}
      {view.correlationId ? <p className={styles.sub}>Reference: {view.correlationId}</p> : null}
      {onRetry ? (
        <button type="button" className={styles.linkbtn} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

function Mark({ match }: { match: Match }) {
  return (
    <span className={styles.mark} aria-hidden="true">
      {MATCH[match].mark}
    </span>
  );
}

function FullItem({
  item,
  busy,
  onDone,
}: {
  item: EvidenceTestItem;
  busy: boolean;
  onDone: (item: EvidenceTestItem) => void;
}) {
  const offCv = item.match !== "missing" && item.source === "records";
  const source = SOURCE[item.source];
  return (
    <li className={offCv ? styles.offcv : MATCH[item.match].className}>
      <Mark match={item.match} />
      <div>
        <span>
          <b>{item.requirement}</b>
          <span className="visually-hidden"> ({MATCH[item.match].label})</span>
          {item.match !== "shown" ? (
            <>
              {" "}
              <button
                type="button"
                className={styles.linkbtn}
                disabled={busy}
                aria-label={`I've done this: ${item.requirement}`}
                title="You've done this, it just isn't on your CV: record it once and every future kit and evidence test can use it"
                onClick={() => onDone(item)}
              >
                I've done this
              </button>
            </>
          ) : null}
        </span>
        {item.quote || source ? (
          <div className={styles.proof}>
            {item.quote ? <blockquote className={styles.quote}>{item.quote}</blockquote> : null}
            {source ? (
              <span className={styles.sourceLine}>
                <span className={`${styles.chip} ${offCv ? styles.chipWarn : ""}`}>{source}</span>
                {item.source === "records" && item.record_title ? (
                  <span className={styles.sub}>{item.record_title}</span>
                ) : null}
              </span>
            ) : null}
          </div>
        ) : null}
        {item.why ? <p className={styles.sub}>{item.why}</p> : null}
      </div>
    </li>
  );
}

/** The role's summary items (no quotes), shown while the full test loads or before it exists. */
function SummaryItems({ items }: { items: NonNullable<Summary["items"]> }) {
  return (
    <ul className={styles.evlist} aria-label="Must-haves">
      {items.map((i) => {
        const offCv = i.match !== "missing" && i.source === "records";
        return (
          <li key={i.requirement} className={offCv ? styles.offcv : MATCH[i.match].className}>
            <Mark match={i.match} />
            <div>
              <span>
                <b>{i.requirement}</b>
                <span className="visually-hidden"> ({MATCH[i.match].label})</span>
              </span>
              {i.source === "none" ? null : offCv ? (
                <span>
                  <span className={`${styles.chip} ${styles.chipWarn}`}>
                    your records · not on CV yet
                  </span>
                </span>
              ) : (
                <span className={styles.sub}>On your CV</span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * job-seeker's evidence block: the verdict line, each must-have with its mark, quote, source and
 * why, "I've done this" on gaps, the team check, Run again and when it was checked.
 */
export function EvidenceBlock(props: EvidenceBlockProps) {
  const {
    variant,
    summary,
    onToggle,
    test,
    loading,
    loadError,
    onRetryLoad,
    running,
    cooling,
    runError,
    onRun,
    notice,
    timeZone,
    doneFor,
    onDone,
    onCancelDone,
    saving,
    saveError,
    onSaveDone,
    contexts,
  } = props;
  const titleId = useId();
  const head = test ?? summary;
  const verdict = head?.verdict;
  const hasGaps = test ? test.items.some((i) => i.match !== "shown") : false;
  const busy = running || saving;

  const runButton = (
    <button
      type="button"
      className={test ? styles.linkbtn : styles.button}
      disabled={running || cooling}
      aria-busy={running || undefined}
      onClick={onRun}
    >
      {running ? "Running…" : test ? "Run again" : "Run the evidence test"}
    </button>
  );

  const live = (
    <p className="visually-hidden" aria-live="polite">
      {notice}
    </p>
  );

  const dialog = doneFor ? (
    <Dialog title={`I've done this: ${doneFor}`} onClose={onCancelDone}>
      <p className={styles.sub}>
        Add a row for each role where you did it, saying what you did in that role. Saved to your
        records and used in letters and CV tweaks. The evidence test runs again when you save.
      </p>
      <ExperienceForm
        name={`Add ${doneFor} to your records`}
        initial={{ ...EMPTY_EXPERIENCE, requirement: doneFor }}
        showRequirement={false}
        contexts={contexts}
        submitLabel="Save and run again"
        busy={saving}
        error={saveError ? <InlineError view={saveError} announce={false} /> : null}
        onSubmit={onSaveDone}
        onCancel={onCancelDone}
      />
    </Dialog>
  ) : null;

  // Nothing to summarise yet: wait for the load, then offer the first run.
  if (!head && loading) {
    return variant === "detail" ? (
      <section className={styles.section} aria-labelledby={titleId} aria-busy="true">
        <h2 id={titleId} className={styles.heading}>
          Evidence test
        </h2>
        <p className={styles.sub} role="status">
          Loading the evidence test…
        </p>
      </section>
    ) : null;
  }
  if (!head && !loadError) {
    const empty = (
      <>
        <p className={styles.none}>
          <span className={styles.sub}>
            Evidence test not run yet. It checks each must-have in the ad against your CVs and
            records.
          </span>{" "}
          {runButton}
        </p>
        {runError ? <InlineError view={runError} /> : null}
        {cooling ? <p className={styles.sub}>Wait a moment before running it again.</p> : null}
        {live}
      </>
    );
    return variant === "detail" ? (
      <section className={styles.section} aria-labelledby={titleId}>
        <h2 id={titleId} className={styles.heading}>
          Evidence test
        </h2>
        {empty}
      </section>
    ) : (
      <div className={styles.emptyCard}>{empty}</div>
    );
  }

  const line = head ? evidenceHeadline(head) : undefined;
  const headline = line ? (
    <>
      <b>{line.verdict}</b> · {line.detail}
    </>
  ) : (
    "Evidence test"
  );

  const body = (
    <>
      {verdict ? (
        <p className={styles.help}>
          {test?.verdict_text || VERDICT_HELP[verdict]}
          {hasGaps ? (
            <>
              {" "}
              Done something that's marked ≈ or ✗ but isn't on your CV? Click <b>I've done this</b>.
            </>
          ) : null}
        </p>
      ) : null}
      {loadError ? (
        <InlineError view={loadError} onRetry={onRetryLoad} />
      ) : test ? (
        <ul className={styles.evlist} aria-label="Must-haves">
          {test.items.map((i) => (
            <FullItem key={i.requirement} item={i} busy={busy} onDone={onDone} />
          ))}
        </ul>
      ) : (
        <>
          {summary?.summary ? <p className={styles.why}>{summary.summary}</p> : null}
          {summary?.items?.length ? <SummaryItems items={summary.items} /> : null}
          {loading ? (
            <p className={styles.sub} role="status">
              Loading the full evidence…
            </p>
          ) : test === null ? (
            <p className={styles.sub}>No full evidence test yet: run it to see the quotes.</p>
          ) : null}
        </>
      )}
      {test ? (
        <p className={`${styles.sub} ${test.team_check.status === "over" ? styles.teamOver : ""}`}>
          Team: {test.team_check.text}
        </p>
      ) : null}
      {runError ? <InlineError view={runError} /> : null}
      <div className={styles.actions}>
        {runButton}
        {cooling ? (
          <span className={styles.sub}>Wait a moment before running it again.</span>
        ) : null}
        {test ? (
          <span className={styles.sub}>
            Checked{" "}
            <time dateTime={test.checked_at}>{formatShortDateTime(test.checked_at, timeZone)}</time>
          </span>
        ) : null}
      </div>
      {live}
      {dialog}
    </>
  );

  const verdictClass = verdict ? (VERDICT_CLASS[verdict] ?? "") : "";
  if (variant === "detail") {
    return (
      <section
        className={`${styles.evidence} ${styles.open} ${verdictClass}`}
        aria-labelledby={titleId}
        aria-busy={running || undefined}
      >
        <h2 id={titleId} className={styles.heading}>
          Evidence test
        </h2>
        <p className={styles.headline}>{headline}</p>
        {body}
      </section>
    );
  }
  return (
    <details
      className={`${styles.evidence} ${verdictClass}`}
      aria-busy={running || undefined}
      onToggle={(e) => onToggle?.(e.currentTarget.open)}
    >
      <summary className={styles.headline}>{headline}</summary>
      {body}
    </details>
  );
}
