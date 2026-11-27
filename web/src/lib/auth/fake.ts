import type { AuthAdapter, SignedInUser, SignInResult } from "./types";

/** The two local accounts, matching the backend's fake auth. Placeholder emails only. */
export const FAKE_USERS = [
  { email: "owner@example.test", label: "Owner (maintainer)" },
  { email: "tester@example.test", label: "Tester (user)" },
] as const;

const STORAGE_KEY = "jr.fakeAuth.email";

/** The header token the backend's AUTH=fake mode accepts: `Authorization: Bearer fake:<email>`. */
export function fakeToken(email: string): string {
  return `fake:${email}`;
}

export interface FakeAuthAdapter extends AuthAdapter {
  signInAs(email: string): Promise<SignInResult>;
}

function isFakeUser(email: string | null): email is string {
  return FAKE_USERS.some((u) => u.email === email);
}

export function createFakeAuth(storage: Storage | null): FakeAuthAdapter {
  const listeners = new Set<(user: SignedInUser | null) => void>();
  let email: string | null = null;
  try {
    const stored = storage?.getItem(STORAGE_KEY) ?? null;
    email = isFakeUser(stored) ? stored : null;
  } catch {
    email = null;
  }

  const set = (next: string | null) => {
    email = next;
    try {
      if (next) storage?.setItem(STORAGE_KEY, next);
      else storage?.removeItem(STORAGE_KEY);
    } catch {
      // Blocked storage only means the session doesn't survive a reload.
    }
    for (const l of listeners) l(next ? { email: next } : null);
  };

  return {
    mode: "fake",
    subscribe(listener) {
      listeners.add(listener);
      listener(email ? { email } : null);
      return () => listeners.delete(listener);
    },
    async getToken() {
      return email ? fakeToken(email) : null;
    },
    async signInAs(candidate) {
      if (!isFakeUser(candidate)) return { kind: "refused" };
      set(candidate);
      return { kind: "signed-in" };
    },
    async signInWithPassword() {
      return { kind: "refused" };
    },
    async verifyTotp() {
      return { kind: "refused" };
    },
    async sendPasswordReset() {
      // Local accounts have no password.
    },
    // Local accounts get no email links, so every code is invalid.
    async checkPasswordResetCode() {
      return { kind: "invalid" };
    },
    async confirmPasswordReset() {
      return { kind: "invalid" };
    },
    async applyEmailAction() {
      return { kind: "invalid" };
    },
    async signOut() {
      set(null);
    },
  };
}
