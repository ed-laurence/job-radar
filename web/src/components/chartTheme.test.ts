import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AXIS, GRID, LEGEND, SERIES, seriesColor, TOOLTIP } from "./chartTheme";

const tokens = readFileSync(resolve(process.cwd(), "src/lib/tokens.css"), "utf8");

describe("chart theme", () => {
  it("uses only chart tokens that tokens.css defines", () => {
    const used = JSON.stringify([SERIES, AXIS, GRID, LEGEND, TOOLTIP]).match(/--[a-z0-9-]+/g) ?? [];
    expect(used.length).toBeGreaterThan(10);
    for (const name of used) {
      expect(name).toMatch(/^--color-chart-/);
      expect(tokens).toContain(`${name}:`);
    }
  });

  it("cycles through the eight series colours", () => {
    expect(SERIES).toHaveLength(8);
    expect(seriesColor(0)).toBe("var(--color-chart-1)");
    expect(seriesColor(9)).toBe("var(--color-chart-2)");
  });
});
