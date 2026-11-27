"use client";

import { type ReactNode, useId, useState } from "react";
import { formatDateTime, useUserSettings } from "@/lib/settings";
import { type Bucket, type Range, useFunnel, useJobInsights } from "./api";
import styles from "./Dashboard.module.css";
import { FunnelChart } from "./FunnelChart";
import { BreakdownChart, InsightTiles, WeeklyApplicationsChart } from "./InsightCharts";
import { PostToApplyChart } from "./PostToApplyChart";
import { RejectionChart } from "./RejectionChart";
import { ScoreChart } from "./ScoreChart";
import { funnelSummary, insightsHeadline } from "./series";

const SPANS = {
  default: { label: "Last 90 days", days: 0 },
  month: { label: "Last 30 days", days: 30 },
  year: { label: "Last 12 months", days: 365 },
} as const;
type Span = keyof typeof SPANS;

function fromDate(span: Span): string | undefined {
  const days = SPANS[span].days;
  if (!days) return undefined;
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

function useRange() {
  const [span, setSpan] = useState<Span>("default");
  const [bucket, setBucket] = useState<Bucket>("week");
  const from = fromDate(span);
  const range: Range = from ? { from, bucket } : { bucket };
  return { span, setSpan, bucket, setBucket, range };
}

function Controls({ state }: { state: ReturnType<typeof useRange> }) {
  const id = useId();
  return (
    <div className={styles.controls}>
      <label className={styles.field} htmlFor={`${id}-span`}>
        Range
        <select
          id={`${id}-span`}
          className={styles.select}
          value={state.span}
          onChange={(e) => state.setSpan(e.target.value as Span)}
        >
          {(Object.keys(SPANS) as Span[]).map((s) => (
            <option key={s} value={s}>
              {SPANS[s].label}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field} htmlFor={`${id}-bucket`}>
        Group by
        <select
          id={`${id}-bucket`}
          className={styles.select}
          value={state.bucket}
          onChange={(e) => state.setBucket(e.target.value as Bucket)}
        >
          <option value="day">Day</option>
          <option value="week">Week</option>
          <option value="month">Month</option>
        </select>
      </label>
    </div>
  );
}

function Section({
  title,
  state,
  meta,
  lead,
  children,
}: {
  title: string;
  state: ReturnType<typeof useRange>;
  meta?: ReactNode;
  lead?: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section className={styles.analytics} aria-labelledby={`${id}-title`}>
      <div className={styles.header}>
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          {meta}
        </div>
        <Controls state={state} />
      </div>
      {lead}
      <div className={styles.charts}>{children}</div>
    </section>
  );
}

/**
 * Job-search insights, in a disclosure directly under the headline tiles (closed by default):
 * outcome rates, then the funnel, timing, scores, rejection reasons and outcome breakdowns.
 */
export function JobAnalytics() {
  const state = useRange();
  const funnel = useFunnel(state.range);
  const insights = useJobInsights(state.range);
  const settings = useUserSettings();
  const headline = [
    insights.data ? insightsHeadline(insights.data) : "",
    funnel.data ? funnelSummary(funnel.data) : "",
  ].filter(Boolean);
  const asOf = insights.data
    ? formatDateTime(insights.data.as_of, settings.data?.display.timezone)
    : "";
  return (
    <details className={`${styles.disclosure} ${styles.analyticsBox}`}>
      <summary>
        Job analytics
        {headline.length ? <span className={styles.sub}> · {headline.join(" · ")}</span> : null}
      </summary>
      <Section
        title="Job analytics"
        state={state}
        meta={asOf ? <p className={styles.sub}>Updated {asOf}</p> : null}
        lead={<InsightTiles range={state.range} />}
      >
        <FunnelChart range={state.range} />
        <PostToApplyChart range={state.range} />
        <ScoreChart range={state.range} />
        <RejectionChart range={state.range} />
        <WeeklyApplicationsChart range={state.range} />
        <BreakdownChart range={state.range} kind="source" />
        <BreakdownChart range={state.range} kind="track" />
        <BreakdownChart range={state.range} kind="band" />
      </Section>
    </details>
  );
}

/** Rejection reasons over time, on the Feedback tab. */
export function RejectionTrends() {
  const state = useRange();
  return (
    <Section title="Rejection trends" state={state}>
      <RejectionChart range={state.range} />
    </Section>
  );
}
