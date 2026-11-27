"use client";

import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, LEGEND, SERIES, TOOLTIP } from "@/components/chartTheme";
import { usePassRate } from "./metricsApi";
import { passRateRows, passRateSummary } from "./passRate";

const cell = (n: number | undefined) => (n === undefined ? "—" : `${n}%`);

/** Agent review pass rate per week, single versus multi-agent (last 90 days). */
export function PassRateChart() {
  const q = usePassRate();
  const rows = q.data ? passRateRows(q.data) : [];
  return (
    <ChartFigure
      title="Agent pass rate"
      {...figureState(q)}
      summary={q.data ? passRateSummary(q.data) : ""}
      columns={["Period", "Single agent", "Multi-agent"]}
      rows={rows.map((r) => [r.period, cell(r.single), cell(r.multi)])}
    >
      <LineChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="period" {...AXIS} />
        <YAxis domain={[0, 100]} unit="%" {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Legend {...LEGEND} />
        <Line
          dataKey="single"
          name="Single agent"
          stroke={SERIES[0]}
          strokeWidth={2}
          connectNulls
        />
        <Line dataKey="multi" name="Multi-agent" stroke={SERIES[1]} strokeWidth={2} connectNulls />
      </LineChart>
    </ChartFigure>
  );
}
