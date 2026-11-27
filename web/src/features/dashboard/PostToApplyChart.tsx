"use client";

import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, SERIES, TOOLTIP } from "@/components/chartTheme";
import { type Range, usePostToApply } from "./api";
import { hours, postToApplyRows, postToApplySummary } from "./series";

export interface PostToApplyChartProps {
  range: Range;
}

export function PostToApplyChart({ range }: PostToApplyChartProps) {
  const q = usePostToApply(range);
  const rows = q.data ? postToApplyRows(q.data) : [];
  return (
    <ChartFigure
      title="Time from posting to applying"
      {...figureState(q)}
      summary={q.data ? postToApplySummary(q.data) : ""}
      columns={["Period", "Median", "Applications"]}
      rows={rows.map((r) => [r.period, hours(r.median), r.count])}
    >
      <LineChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="period" {...AXIS} />
        <YAxis {...AXIS} unit=" h" />
        <Tooltip {...TOOLTIP} />
        <Line dataKey="median" name="Median hours" stroke={SERIES[0]} strokeWidth={2} dot={false} />
      </LineChart>
    </ChartFigure>
  );
}
