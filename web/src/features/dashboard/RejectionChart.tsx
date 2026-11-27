"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, SERIES, TOOLTIP } from "@/components/chartTheme";
import { type Range, useRejections } from "./api";
import { rejectionRows, rejectionSummary } from "./series";

export interface RejectionChartProps {
  range: Range;
}

export function RejectionChart({ range }: RejectionChartProps) {
  const q = useRejections(range);
  const rows = q.data ? rejectionRows(q.data) : [];
  return (
    <ChartFigure
      title="Rejection reasons"
      {...figureState(q)}
      summary={q.data ? rejectionSummary(q.data) : ""}
      columns={["Reason", "Count"]}
      rows={rows.map((r) => [r.label, r.count])}
    >
      <BarChart responsive data={rows} layout="vertical" accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis type="number" allowDecimals={false} {...AXIS} />
        <YAxis type="category" dataKey="label" width={130} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="count" name="Rejections" fill={SERIES[6]} />
      </BarChart>
    </ChartFigure>
  );
}
