import {
  PHASE_DEVELOPMENT_SERVER,
  PHASE_PRODUCTION_BUILD,
  PHASE_PRODUCTION_SERVER,
} from "next/constants";
import { describe, expect, it } from "vitest";
import config, { distDirFor, PRODUCTION_DIST_DIR } from "../../next.config";

describe("next dist dir", () => {
  it("keeps the dev server on .next and builds into a separate dir", () => {
    expect(distDirFor(PHASE_DEVELOPMENT_SERVER, undefined)).toBe(".next");
    expect(distDirFor(PHASE_PRODUCTION_BUILD, undefined)).toBe(PRODUCTION_DIST_DIR);
    // next start serves what next build wrote.
    expect(distDirFor(PHASE_PRODUCTION_SERVER, undefined)).toBe(PRODUCTION_DIST_DIR);
    expect(PRODUCTION_DIST_DIR).not.toBe(".next");
  });

  it("lets NEXT_DIST_DIR override every phase", () => {
    expect(distDirFor(PHASE_PRODUCTION_BUILD, "custom")).toBe("custom");
    expect(distDirFor(PHASE_DEVELOPMENT_SERVER, "custom")).toBe("custom");
  });

  it("keeps the rest of the config the same in every phase", async () => {
    const dev = config(PHASE_DEVELOPMENT_SERVER);
    const build = config(PHASE_PRODUCTION_BUILD);
    const { distDir: _d, headers: devHeaders, ...devRest } = dev;
    const { distDir: _b, headers: buildHeaders, ...buildRest } = build;
    expect(buildRest).toEqual(devRest);
    expect(await buildHeaders?.()).toEqual(await devHeaders?.());
  });
});
