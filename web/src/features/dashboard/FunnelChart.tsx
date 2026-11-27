"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, SERIES, TOOLTIP } from "@/components/chartTheme";
import { type Range, useFunnel } from "./api";
import { funnelRows, funnelSummary } from "./series";

export interface FunnelChartProps {
  range: Range;
}

export function FunnelChart({ range }: FunnelChartProps) {
  const q = useFunnel(range);
  const rows = q.data ? funnelRows(q.data) : [];
  return (
    <ChartFigure
      title="Application funnel"
      {...figureState(q)}
      summary={q.data ? funnelSummary(q.data) : ""}
      columns={["Status", "Roles"]}
      rows={rows.map((r) => [r.label, r.count])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis allowDecimals={false} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="count" name="Roles" fill={SERIES[0]} />
      </BarChart>
    </ChartFigure>
  );
}
