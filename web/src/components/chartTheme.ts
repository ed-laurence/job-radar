/** Recharts styling from the chart tokens in lib/tokens.css, so both themes work (CODE-24). */
export const SERIES = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
  "var(--color-chart-6)",
  "var(--color-chart-7)",
  "var(--color-chart-8)",
] as const;

/** Series colour `index`, wrapping after eight. */
export function seriesColor(index: number): string {
  return SERIES[index % SERIES.length] ?? SERIES[0];
}

export const TICK = { fill: "var(--color-chart-axis)", fontSize: 12 };
const AXIS_LINE = { stroke: "var(--color-chart-grid)" };
/** Spread on XAxis/YAxis: labels in the axis colour, lines in the grid colour. */
export const AXIS = { tick: TICK, axisLine: AXIS_LINE, tickLine: AXIS_LINE };
export const GRID = { stroke: "var(--color-chart-grid)", strokeDasharray: "3 3" };
export const TOOLTIP = {
  contentStyle: {
    background: "var(--color-chart-tooltip-bg)",
    borderColor: "var(--color-chart-tooltip-border)",
    color: "var(--color-chart-tooltip-text)",
  },
  labelStyle: { color: "var(--color-chart-tooltip-text)" },
  cursor: { fill: "var(--color-chart-cursor)" },
};
export const LEGEND = { wrapperStyle: { color: "var(--color-chart-axis)", fontSize: 12 } };
