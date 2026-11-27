"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, SERIES, TOOLTIP } from "@/components/chartTheme";
import { type Range, useScores } from "./api";
import { scoreRows, scoreSummary } from "./series";

export interface ScoreChartProps {
  range: Range;
}

export function ScoreChart({ range }: ScoreChartProps) {
  const q = useScores(range);
  const rows = q.data ? scoreRows(q.data) : [];
  return (
    <ChartFigure
      title="Score distribution"
      {...figureState(q)}
      summary={q.data ? scoreSummary(q.data) : ""}
      columns={["Score band", "Roles"]}
      rows={rows.map((r) => [r.label, r.count])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis allowDecimals={false} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="count" name="Roles" fill={SERIES[2]} />
      </BarChart>
    </ChartFigure>
  );
}
