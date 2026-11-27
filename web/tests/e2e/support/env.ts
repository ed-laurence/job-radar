import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * E2E settings from the environment, falling back to ~/.config/jobradar/local.env (the file
 * scripts/dev.sh loads). Only the keys e2e needs are read from the file, the environment wins,
 * and values are never logged: callers only learn whether a key is set.
 */
export const LOCAL_ENV = join(homedir(), ".config", "jobradar", "local.env");

const WANTED = /^(E2E_[A-Z_]+|NEXT_PUBLIC_FIREBASE_API_KEY)$/;

/** Parses `KEY=value` lines (optional `export`, quotes and comments), keeping wanted keys only. */
export function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match) continue;
    const [, key = "", rest = ""] = match;
    if (!WANTED.test(key)) continue;
    let value = rest.trim();
    const quote = value[0];
    if ((quote === '"' || quote === "'") && value.endsWith(quote) && value.length >= 2) {
      value = value.slice(1, -1);
    } else {
      value = value.replace(/\s+#.*$/, "");
    }
    out[key] = value;
  }
  return out;
}

let loaded = false;

/** Copies wanted keys from local.env into process.env where they aren't already set. */
export function loadLocalEnv(): void {
  if (loaded) return;
  loaded = true;
  if (!existsSync(LOCAL_ENV)) return;
  let parsed: Record<string, string>;
  try {
    parsed = parseEnv(readFileSync(LOCAL_ENV, "utf8"));
  } catch {
    return;
  }
  for (const [key, value] of Object.entries(parsed)) {
    if (!process.env[key]) process.env[key] = value;
  }
}

export type Account = "owner" | "tester";

export interface Credentials {
  email: string;
  password: string;
}

const VARS: Readonly<Record<Account, readonly [string, string]>> = {
  owner: ["E2E_OWNER_EMAIL", "E2E_OWNER_PASSWORD"],
  tester: ["E2E_TESTER_EMAIL", "E2E_TESTER_PASSWORD"],
};

/** The variable names an account needs (for skip reasons; never the values). */
export function credentialVars(account: Account): readonly string[] {
  return VARS[account];
}

export function credentials(account: Account): Credentials | undefined {
  loadLocalEnv();
  const [emailVar, passwordVar] = VARS[account];
  const email = process.env[emailVar];
  const password = process.env[passwordVar];
  return email && password ? { email, password } : undefined;
}

/** Why the given accounts can't sign in, or undefined when all their variables are set. */
export function missingCredentials(...accounts: Account[]): string | undefined {
  const missing = accounts.flatMap((a) =>
    credentials(a) ? [] : VARS[a].filter((name) => !process.env[name]),
  );
  if (!missing.length) return undefined;
  return `real sign-in needs ${missing.join(", ")} (environment or ${LOCAL_ENV}); not set, so skipped`;
}
