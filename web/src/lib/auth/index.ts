import type { AuthConfig } from "./config";
import { createFakeAuth } from "./fake";
import { createIdentityAuth } from "./identity";
import type { AuthAdapter } from "./types";

export type { AuthState, AuthStatus } from "./AuthProvider";
export { AuthProvider, useAuth } from "./AuthProvider";
export type { AuthConfig, AuthMode } from "./config";
export { readAuthConfig, resolveAuthMode } from "./config";
export type { FakeAuthAdapter } from "./fake";
export { createFakeAuth, FAKE_USERS, fakeToken } from "./fake";
export { safeRedirectPath } from "./safeRedirect";
export type {
  AuthAdapter,
  EmailActionMode,
  EmailActionResult,
  ResetCodeCheck,
  SignedInUser,
  SignInResult,
} from "./types";

function sessionStorageOrNull(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function createAuthAdapter(config: AuthConfig): AuthAdapter {
  return config.mode === "fake"
    ? createFakeAuth(sessionStorageOrNull())
    : createIdentityAuth(config);
}

export function isFakeAuth(adapter: AuthAdapter): adapter is import("./fake").FakeAuthAdapter {
  return adapter.mode === "fake";
}
