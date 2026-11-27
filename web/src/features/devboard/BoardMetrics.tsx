"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, SERIES, TOOLTIP } from "@/components/chartTheme";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import styles from "./DevBoard.module.css";
import {
  columnTimeRows,
  columnTimeSummary,
  type DevMetrics,
  hours,
  outcomeRows,
  outcomeSummary,
  throughputRows,
  throughputSummary,
  tiles,
  wipRows,
  wipSummary,
} from "./metrics";
import { useDevMetrics } from "./metricsApi";
import { PassRateChart } from "./PassRateChart";

interface CountChartProps {
  title: string;
  q: ReturnType<typeof useDevMetrics>;
  rows: { label: string; count: number }[];
  summary: string;
  columns: [string, string];
  color: string;
}

function CountChart({ title, q, rows, summary, columns, color }: CountChartProps) {
  return (
    <ChartFigure
      title={title}
      {...figureState(q)}
      summary={summary}
      columns={columns}
      rows={rows.map((r) => [r.label, r.count])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis allowDecimals={false} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="count" name={columns[1]} fill={color} />
      </BarChart>
    </ChartFigure>
  );
}

function Metrics() {
  const q = useDevMetrics();
  const m: DevMetrics | undefined = q.data;
  const time = m ? columnTimeRows(m) : [];
  return (
    <div className={styles.metrics}>
      {q.isPending ? (
        <LoadingState label="Loading board metrics…" />
      ) : q.isError ? (
        <ErrorState
          title="Board metrics failed to load"
          correlationId={isApiError(q.error) ? q.error.correlationId : undefined}
          onRetry={() => void q.refetch()}
        />
      ) : (
        <dl className={styles.kpis}>
          {tiles(q.data).map(([label, value]) => (
            <div key={label} className={styles.kpi}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className={styles.charts}>
        <CountChart
          title="Throughput per week"
          q={q}
          rows={m ? throughputRows(m) : []}
          summary={m ? throughputSummary(m) : ""}
          columns={["Week", "Cards done"]}
          color={SERIES[0]}
        />
        <CountChart
          title="Work in progress by column"
          q={q}
          rows={m ? wipRows(m) : []}
          summary={m ? wipSummary(m) : ""}
          columns={["Column", "Cards"]}
          color={SERIES[1]}
        />
        <ChartFigure
          title="Median hours per column"
          {...figureState(q)}
          summary={m ? columnTimeSummary(m) : ""}
          columns={["Column", "Median time"]}
          rows={time.map((r) => [r.label, hours(r.hours)])}
        >
          <BarChart responsive data={time} accessibilityLayer={false}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="label" {...AXIS} />
            <YAxis unit=" h" {...AXIS} />
            <Tooltip {...TOOLTIP} />
            <Bar dataKey="hours" name="Median hours" fill={SERIES[2]} />
          </BarChart>
        </ChartFigure>
        <CountChart
          title="Agent run outcomes"
          q={q}
          rows={m ? outcomeRows(m) : []}
          summary={m ? outcomeSummary(m) : ""}
          columns={["Outcome", "Runs"]}
          color={SERIES[3]}
        />
        <PassRateChart />
      </div>
    </div>
  );
}

/**
 * Flow and agent metrics above the board, collapsed by default so the board stays first. The
 * metrics load only once opened.
 */
export function BoardMetrics() {
  const [open, setOpen] = useState(false);
  return (
    <details
      className={styles.metricsBox}
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className={styles.metricsSummary}>Board metrics</summary>
      {open ? <Metrics /> : null}
    </details>
  );
}
