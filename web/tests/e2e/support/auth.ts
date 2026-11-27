import { spawnSync } from "node:child_process";
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";
import { type Account, credentials, missingCredentials } from "./env";

/**
 * Real sign-in for every e2e spec (Identity Platform, email + password). Each account signs in
 * once per run through the app's sign-in page, in a separate untraced process (sign-in.mjs), and
 * its session is saved under web/.artifacts/e2e/auth/ (gitignored, mode 0600). Specs then start
 * signed in by restoring that session into their page. No credential is ever written to disk,
 * typed in a traced page, or logged.
 */

export type { Account } from "./env";

export const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

const AUTH_DIR = fileURLToPath(new URL("../../../.artifacts/e2e/auth/", import.meta.url));
const SIGN_IN_SCRIPT = fileURLToPath(new URL("./sign-in.mjs", import.meta.url));
/** Re-sign in when the saved ID token has less than this left (the API rejects expired ones). */
const MIN_TOKEN_LIFE_MS = 10 * 60_000;
const LOCK_STALE_MS = 2 * 60_000;
const WAIT_MS = 90_000;

interface SavedSession {
  origin: string;
  /** The runner process that signed in: sessions are reused within one run only. */
  run: number;
  state: {
    cookies: Parameters<import("@playwright/test").BrowserContext["addCookies"]>[0];
    origins: { origin: string; localStorage: { name: string; value: string }[] }[];
  };
  session: [string, string][];
}

interface FirebaseUser {
  stsTokenManager?: { accessToken?: string; expirationTime?: number };
}

/** Workers of one `playwright test` run share the runner as their parent process. */
const RUN = process.ppid;

const fileFor = (account: Account) => `${AUTH_DIR}${account}.json`;
const failedFile = (account: Account) => `${AUTH_DIR}${account}.failed.json`;

/** A sign-in that already failed in this run (its reason), so it isn't attempted again. */
function failedThisRun(account: Account): string | undefined {
  try {
    const failed = JSON.parse(readFileSync(failedFile(account), "utf8")) as {
      run?: number;
      reason?: string;
    };
    return failed.run === RUN ? (failed.reason ?? "sign-in failed") : undefined;
  } catch {
    return undefined;
  }
}

function firebaseUser(saved: SavedSession): FirebaseUser | undefined {
  const entry = saved.session.find(([k]) => k.startsWith("firebase:authUser:"));
  if (!entry) return undefined;
  try {
    return JSON.parse(entry[1]) as FirebaseUser;
  } catch {
    return undefined;
  }
}

function readSaved(account: Account): SavedSession | undefined {
  try {
    const saved = JSON.parse(readFileSync(fileFor(account), "utf8")) as SavedSession;
    const exp = firebaseUser(saved)?.stsTokenManager?.expirationTime ?? 0;
    const usable =
      saved.run === RUN &&
      saved.origin === new URL(BASE_URL).origin &&
      exp - Date.now() > MIN_TOKEN_LIFE_MS;
    return usable ? saved : undefined;
  } catch {
    return undefined;
  }
}

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Runs the real sign-in in its own process; the credentials go only into its environment. */
function signInProcess(account: Account): void {
  const creds = credentials(account);
  if (!creds) throw new Error(missingCredentials(account));
  const out = `${fileFor(account)}.new`;
  const result = spawnSync(process.execPath, [SIGN_IN_SCRIPT, BASE_URL, out], {
    env: { ...process.env, E2E_SIGNIN_EMAIL: creds.email, E2E_SIGNIN_PASSWORD: creds.password },
    encoding: "utf8",
    timeout: WAIT_MS,
  });
  if (result.status !== 0) {
    const reason = `${account} sign-in failed: ${(result.stderr || "no output").trim()}`;
    // Remember the failure for this run: retrying per test would only pile up failed attempts
    // against Identity Platform (and could lock the account).
    writeFileSync(failedFile(account), JSON.stringify({ run: RUN, reason }), { mode: 0o600 });
    throw new Error(reason);
  }
  // Stamp the run so the next run signs in again rather than trusting an old session.
  const saved = JSON.parse(readFileSync(out, "utf8")) as Omit<SavedSession, "run">;
  writeFileSync(out, JSON.stringify({ ...saved, run: RUN }), { mode: 0o600 });
  renameSync(out, fileFor(account));
}

/**
 * The account's session for this run: reused when saved, else signed in once (a lock file keeps
 * parallel workers from signing in together; the others wait for the saved session).
 */
export async function ensureSession(account: Account): Promise<SavedSession> {
  mkdirSync(AUTH_DIR, { recursive: true });
  const lock = `${fileFor(account)}.lock`;
  const deadline = Date.now() + WAIT_MS;
  for (;;) {
    const saved = readSaved(account);
    if (saved) return saved;
    const failed = failedThisRun(account);
    if (failed) throw new Error(`${failed} (earlier in this run; not retried)`);
    let fd: number | undefined;
    try {
      fd = openSync(lock, "wx");
    } catch {
      // Another worker is signing in; take over a lock left by a crashed one.
      if (existsSync(lock) && Date.now() - statSync(lock).mtimeMs > LOCK_STALE_MS) {
        unlinkSync(lock);
        continue;
      }
      if (Date.now() > deadline) throw new Error(`${account} sign-in: timed out waiting`);
      await pause(500);
      continue;
    }
    try {
      closeSync(fd);
      if (!readSaved(account) && !failedThisRun(account)) signInProcess(account);
    } finally {
      unlinkSync(lock);
    }
  }
}

/** Skips the current test (or describe, from beforeAll) when an account has no credentials. */
export function requireAccounts(...accounts: Account[]): void {
  const reason = missingCredentials(...accounts);
  test.skip(Boolean(reason), reason ?? "");
}

/**
 * Starts `page` signed in as `account` (the session from the real sign-in page) and opens
 * `path`. Skips the test when the account's E2E_* variables are missing.
 */
export async function signIn(page: Page, account: Account, path = "/dashboard"): Promise<void> {
  requireAccounts(account);
  const saved = await ensureSession(account);
  const context = page.context();
  if (saved.state.cookies.length) await context.addCookies(saved.state.cookies);
  const local = saved.state.origins.find((o) => o.origin === saved.origin)?.localStorage ?? [];
  await context.addInitScript(
    ({ origin, session, local }) => {
      if (window.location.origin !== origin) return;
      // Only fill what's missing, so the app's own token refreshes and sign-out stick.
      if (!window.sessionStorage.getItem("jr.e2e.restored")) {
        window.sessionStorage.setItem("jr.e2e.restored", "1");
        for (const [k, v] of session) window.sessionStorage.setItem(k, v);
        for (const { name, value } of local) window.localStorage.setItem(name, value);
      }
    },
    { origin: saved.origin, session: saved.session, local },
  );
  await page.goto(path);
  await expect(page).not.toHaveURL(/\/sign-in/);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible({ timeout: 30_000 });
}

/**
 * A real ID token for API calls (cleanup, setup), from the account's Firebase session that the
 * sign-in page created. Read in Node from the saved session, so it never passes through a traced
 * page; a session near expiry is replaced by signing in again.
 */
export async function idToken(account: Account): Promise<string> {
  const token = firebaseUser(await ensureSession(account))?.stsTokenManager?.accessToken;
  if (!token) throw new Error(`${account}: the saved session has no ID token`);
  return token;
}
