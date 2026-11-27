"use client";

import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, LEGEND, SERIES, seriesColor, TOOLTIP } from "@/components/chartTheme";
import { type Range, useJobInsights } from "./api";
import styles from "./Dashboard.module.css";
import {
  type BreakdownKind,
  breakdownRows,
  breakdownSummary,
  OUTCOMES,
  rateTiles,
  weeklyRows,
  weeklySummary,
} from "./series";

export interface InsightProps {
  range: Range;
}

/** Response, interview, offer and ghost rates, days to response and follow-ups on time. */
export function InsightTiles({ range }: InsightProps) {
  const q = useJobInsights(range);
  if (!q.data) return null;
  return (
    <section aria-label="Outcome rates">
      <dl className={`${styles.kpis} ${styles.rates}`}>
        {rateTiles(q.data).map((t) => (
          <div key={t.label} className={styles.kpi}>
            <dt>{t.label}</dt>
            <dd>{t.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function WeeklyApplicationsChart({ range }: InsightProps) {
  const q = useJobInsights(range);
  const rows = q.data ? weeklyRows(q.data) : [];
  return (
    <ChartFigure
      title="Applications per week"
      {...figureState(q)}
      summary={q.data ? weeklySummary(q.data) : ""}
      columns={["Week starting", "Applications"]}
      rows={rows.map((r) => [r.week, r.applied])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="week" {...AXIS} />
        <YAxis allowDecimals={false} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="applied" name="Applications" fill={SERIES[2]} />
      </BarChart>
    </ChartFigure>
  );
}

const BREAKDOWNS: Record<BreakdownKind, { title: string; column: string }> = {
  source: { title: "Outcomes by source", column: "Source" },
  track: { title: "Outcomes by track", column: "Track" },
  band: { title: "Outcomes by score band", column: "Score band" },
};

export interface BreakdownChartProps extends InsightProps {
  kind: BreakdownKind;
}

/** Found, applied, interviews and rejections side by side for each group. */
export function BreakdownChart({ range, kind }: BreakdownChartProps) {
  const q = useJobInsights(range);
  const items = q.data
    ? kind === "source"
      ? q.data.by_source
      : kind === "track"
        ? q.data.by_track
        : q.data.score_bands
    : [];
  const rows = breakdownRows(kind, items);
  const { title, column } = BREAKDOWNS[kind];
  return (
    <ChartFigure
      title={title}
      {...figureState(q)}
      summary={q.data ? breakdownSummary(kind, items) : ""}
      columns={[column, ...OUTCOMES.map((o) => o.label)]}
      rows={rows.map((r) => [r.label, ...OUTCOMES.map((o) => r[o.key])])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis allowDecimals={false} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Legend {...LEGEND} />
        {OUTCOMES.map((o, i) => (
          <Bar key={o.key} dataKey={o.key} name={o.label} fill={seriesColor(i)} />
        ))}
      </BarChart>
    </ChartFigure>
  );
}
