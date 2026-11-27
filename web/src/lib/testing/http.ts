import { HttpResponse, http } from "msw";
import type { Problem, Schemas } from "@/lib/api";

/** Same origin as the app, so `lib/sse.ts` (which uses the default `/api` base) hits MSW too. */
export const API = `${window.location.origin}/api`;

export const OWNER = "owner@example.test";
export const TESTER = "tester@example.test";

export const ME: Readonly<Record<string, Schemas["Me"]>> = {
  [OWNER]: { id: "00000000-0000-4000-8000-000000000001", email: OWNER, role: "maintainer" },
  [TESTER]: { id: "00000000-0000-4000-8000-000000000002", email: TESTER, role: "user" },
};

/** A problem+json body. `type` is the slug after `/problems/`. */
export function problem(status: number, type: string, extra: Partial<Problem> = {}): Problem {
  return {
    type: `/problems/${type}`,
    title: type.replace(/-/g, " "),
    status,
    correlation_id: "corr-test-0001",
    ...extra,
  };
}

/** The same problem as a complete HTTP response, for MSW handlers. */
export function problemResponse(status: number, type: string, extra: Partial<Problem> = {}) {
  return HttpResponse.json(problem(status, type, extra), {
    status,
    headers: { "content-type": "application/problem+json" },
  });
}

/** `GET /me` for the two fake accounts, keyed by the fake bearer token. */
export const meHandler = http.get(`${API}/me`, ({ request }) => {
  const email = request.headers.get("authorization")?.replace("Bearer fake:", "") ?? "";
  const me = ME[email];
  return me ? HttpResponse.json(me) : problemResponse(401, "unauthenticated");
});
