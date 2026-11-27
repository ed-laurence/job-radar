import type { Schemas } from "@/lib/api";

/** Role fixtures for the roles tests. Render helpers live in `@/lib/testing`. */
export const ROLE_ID = "00000000-0000-4000-8000-0000000000a1";

export function makeRole(overrides: Partial<Schemas["Role"]> = {}): Schemas["Role"] {
  return {
    id: ROLE_ID,
    source: "manual",
    title: "Senior Data Engineer",
    company: "Acme Pty Ltd",
    location: "Sydney NSW",
    work_mode: "hybrid",
    stage: "scored",
    status: "reviewing",
    score: 78,
    found_at: "2026-11-20T10:15:00Z",
    rank: "a0",
    version: 3,
    created_at: "2026-11-20T10:15:00Z",
    updated_at: "2026-11-21T08:00:00Z",
    ...overrides,
  };
}
