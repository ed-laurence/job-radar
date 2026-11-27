import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Vitest runs from web/.
const css = readFileSync(resolve(process.cwd(), "src/lib/tokens.css"), "utf8").split("\r").join("");

/** The custom properties declared directly inside the first rule whose selector is `selector`. */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no rule for ${selector}`);
  const body = css.slice(start + selector.length + 2, css.indexOf("}", start));
  return Object.fromEntries(
    [...body.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], (m[2] ?? "").trim()]),
  );
}

const THEMES = {
  light: block(":root"),
  "dark (system)": block(':root:not([data-theme="light"])'),
  "dark (forced)": block(':root[data-theme="dark"]'),
};

const SERIES = Array.from({ length: 8 }, (_, i) => `--color-chart-${i + 1}`);
const CHROME = [
  "--color-chart-grid",
  "--color-chart-axis",
  "--color-chart-tooltip-bg",
  "--color-chart-tooltip-text",
  "--color-chart-tooltip-border",
  "--color-chart-cursor",
];

function luminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m?.[1]) throw new Error(`expected #rrggbb, got ${hex}`);
  const value = m[1];
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = Number.parseInt(value.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

/** A token's colour in a theme; dark blocks only override, so fall back to the light value. */
function color(theme: Record<string, string>, name: string): string {
  const value = theme[name] ?? THEMES.light[name];
  if (!value) throw new Error(`${name} is not defined`);
  return value;
}

describe("chart colour tokens", () => {
  it.each(Object.entries(THEMES))("are all defined in the %s theme", (_, theme) => {
    for (const name of [...SERIES, ...CHROME]) expect(theme[name], name).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("are the same in both dark-theme blocks", () => {
    for (const name of [...SERIES, ...CHROME]) {
      expect(THEMES["dark (forced)"][name], name).toBe(THEMES["dark (system)"][name]);
    }
  });

  it.each(Object.entries(THEMES))("meet WCAG AA in the %s theme", (_, theme) => {
    const backgrounds = [color(theme, "--color-bg"), color(theme, "--color-surface")];
    for (const bg of backgrounds) {
      // Graphical objects need 3:1 (WCAG 1.4.11); axis text needs 4.5:1 (1.4.3).
      for (const name of SERIES) expect(contrast(color(theme, name), bg), name).toBeGreaterThan(3);
      expect(contrast(color(theme, "--color-chart-axis"), bg)).toBeGreaterThan(4.5);
    }
    expect(
      contrast(
        color(theme, "--color-chart-tooltip-text"),
        color(theme, "--color-chart-tooltip-bg"),
      ),
    ).toBeGreaterThan(4.5);
  });

  it.each(Object.entries(THEMES))(
    "are eight distinct series colours in the %s theme",
    (_, theme) => {
      expect(new Set(SERIES.map((name) => color(theme, name).toLowerCase())).size).toBe(8);
    },
  );
});
