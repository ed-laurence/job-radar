"use client";

import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, LEGEND, seriesColor, TOOLTIP } from "@/components/chartTheme";
import { useSpend } from "./api";
import { aud, spendByFeature, spendFeatureSummary } from "./spend";

export function SpendByFeatureChart() {
  const q = useSpend(true);
  const { keys, rows } = q.data ? spendByFeature(q.data) : { keys: [], rows: [] };
  return (
    <ChartFigure
      title="LLM spend by feature"
      {...figureState(q)}
      summary={q.data ? spendFeatureSummary(q.data) : ""}
      columns={["Period", ...keys]}
      rows={rows.map((r) => [String(r.period), ...keys.map((k) => aud(Number(r[k] ?? 0)))])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="period" {...AXIS} />
        <YAxis {...AXIS} unit="$" />
        <Tooltip {...TOOLTIP} />
        <Legend {...LEGEND} />
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} stackId="spend" fill={seriesColor(i)} />
        ))}
      </BarChart>
    </ChartFigure>
  );
}
