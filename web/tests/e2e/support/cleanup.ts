import type { Page } from "@playwright/test";
import { type Account, BASE_URL, idToken } from "./auth";

/**
 * Test-data cleanup for e2e specs: every role, contact and task a test creates is deleted again
 * afterwards, through the API as the owner (a real ID token from the owner's signed-in session),
 * so runs don't pile data on the board.
 */

export { BASE_URL } from "./auth";

export type Kind = "roles" | "contacts" | "tasks";
const KINDS: readonly Kind[] = ["roles", "contacts", "tasks"];

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * An API call as `account` (default the owner) through the BFF; waits out a 429 (Retry-After) a
 * few times rather than fail the cleanup.
 */
export async function api(
  path: string,
  init: RequestInit = {},
  account: Account = "owner",
): Promise<Response> {
  const auth = { Authorization: `Bearer ${await idToken(account)}` };
  for (let attempt = 0; ; attempt++) {
    const res = await fetch(`${BASE_URL}/api${path}`, {
      ...init,
      headers: { ...(init.headers ?? {}), ...auth },
      signal: AbortSignal.timeout(15_000),
    });
    if (res.status !== 429 || attempt >= 5) return res;
    const wait = Number(res.headers.get("retry-after") ?? 5);
    await pause(Math.min(Math.max(wait, 1), 60) * 1000);
  }
}

/**
 * Soft-deletes one item. Deletes need the current version: try the one we know (the create's,
 * normally 1), and on a 409 take `current_version` from the problem and try again, so an item
 * costs one or two calls of the owner's rate budget. Already gone counts as done.
 */
export async function deleteItem(kind: Kind, id: string, knownVersion = 1): Promise<void> {
  let version = knownVersion;
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await api(`/${kind}/${id}?version=${version}`, { method: "DELETE" });
    if (res.ok || res.status === 404) return;
    if (res.status !== 409) throw new Error(`cleanup: DELETE ${kind}/${id} answered ${res.status}`);
    const problem = (await res.json().catch(() => ({}))) as { current_version?: number };
    if (typeof problem.current_version !== "number") {
      throw new Error(`cleanup: DELETE ${kind}/${id} conflicted without a current version`);
    }
    version = problem.current_version;
  }
  throw new Error(`cleanup: ${kind}/${id} kept changing; not deleted`);
}

/** The part of a create's answer cleanup needs. */
export interface CreatedBody {
  id?: string;
  version?: number;
}

const CREATED = new RegExp(`/api/(${KINDS.join("|")})$`);

/**
 * Collects what a test creates and deletes it on `cleanup()`. Items made through the API are
 * added with `add`; items made through the UI are picked up by `watch(page)`, which records every
 * successful create the page sends.
 */
export class Created {
  private readonly items = new Map<string, { kind: Kind; version: number }>();

  /** Records an item to delete; `version` is the one its create returned. */
  add(kind: Kind, id: string, version = 1): void {
    this.items.set(id, { kind, version });
  }

  /**
   * Routes the page's creates through Playwright so the new id is read before the page gets the
   * answer. (A `response` listener can miss it: a reload right after a create discards the body.)
   */
  async watch(page: Page): Promise<void> {
    await page.route(
      (url) => CREATED.test(url.pathname),
      async (route) => {
        const match = CREATED.exec(new URL(route.request().url()).pathname);
        const kind = match?.[1] as Kind | undefined;
        if (!kind || route.request().method() !== "POST") return route.fallback();
        const response = await route.fetch();
        if (response.status() === 201) {
          const body = (await response.json().catch(() => ({}))) as CreatedBody;
          if (body.id) this.add(kind, body.id, body.version);
        }
        return route.fulfill({ response });
      },
    );
  }

  /** Deletes everything recorded so far. Reports every failure together, after trying all. */
  async cleanup(): Promise<void> {
    const items = [...this.items];
    this.items.clear();
    if (!items.length) return;
    const results = await Promise.allSettled(
      items.map(([id, { kind, version }]) => deleteItem(kind, id, version)),
    );
    const errors = results.flatMap((r) => (r.status === "rejected" ? [String(r.reason)] : []));
    if (errors.length) throw new Error(errors.join("\n"));
  }
}
