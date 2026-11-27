"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFigure, figureState } from "@/components/ChartFigure";
import { AXIS, GRID, SERIES, TOOLTIP } from "@/components/chartTheme";
import { useSpend } from "./api";
import { aud, spendByProvider, spendProviderSummary } from "./spend";

export function SpendByProviderChart() {
  const q = useSpend(true);
  const rows = q.data ? spendByProvider(q.data) : [];
  return (
    <ChartFigure
      title="LLM spend by provider"
      {...figureState(q)}
      summary={q.data ? spendProviderSummary(q.data) : ""}
      columns={["Provider", "Spend (AUD)", "Calls"]}
      rows={rows.map((r) => [r.provider, aud(r.cost), r.calls])}
    >
      <BarChart responsive data={rows} accessibilityLayer={false}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="provider" {...AXIS} />
        <YAxis {...AXIS} unit="$" />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="cost" name="Spend (AUD)" fill={SERIES[3]} />
      </BarChart>
    </ChartFigure>
  );
}
