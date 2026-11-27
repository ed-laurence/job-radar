import type { NextConfig } from "next";
import { PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER } from "next/constants";
import { SECURITY_HEADERS } from "./src/lib/csp";

/**
 * Production builds (and `next start`, which serves them) use their own dist dir, so running the
 * build gate never rewrites the `.next` a running `next dev` uses (that caused HMR hash-mismatch
 * reload loops). `NEXT_DIST_DIR` overrides it for every phase.
 */
export const PRODUCTION_DIST_DIR = ".next-build";

export function distDirFor(phase: string, override: string | undefined): string {
  if (override) return override;
  return phase === PHASE_PRODUCTION_BUILD || phase === PHASE_PRODUCTION_SERVER
    ? PRODUCTION_DIST_DIR
    : ".next";
}

export default function config(phase: string): NextConfig {
  return {
    distDir: distDirFor(phase, process.env.NEXT_DIST_DIR || undefined),
    reactStrictMode: true,
    poweredByHeader: false,
    // Agent instructions live only in the root AGENTS.md (single source of truth).
    agentRules: false,
    // No source maps in the production browser bundle (PLAN: data exposure).
    productionBrowserSourceMaps: false,
    env: {
      // The backend's AUTH=fake switch also selects fake sign-in in the web app.
      NEXT_PUBLIC_AUTH_MODE: process.env.NEXT_PUBLIC_AUTH_MODE ?? process.env.AUTH ?? "",
    },
    async headers() {
      return [{ source: "/:path*", headers: SECURITY_HEADERS }];
    },
  };
}
