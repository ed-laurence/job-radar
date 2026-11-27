export type AuthMode = "fake" | "identity";

export interface AuthConfig {
  mode: AuthMode;
  /** Identity Platform web API key: public by design (referrer- and API-restricted). */
  firebaseApiKey: string | undefined;
  tenantId: string | undefined;
}

/**
 * Explicit `fake` or `identity` wins. Unset means fake in dev/test and identity in production
 * builds, so a production bundle never falls back to fake auth by accident.
 */
export function resolveAuthMode(raw: string | undefined, nodeEnv: string | undefined): AuthMode {
  if (raw === "fake" || raw === "identity") return raw;
  return nodeEnv === "production" ? "identity" : "fake";
}

export function readAuthConfig(): AuthConfig {
  // Literal property access so Next can inline these NEXT_PUBLIC_* values at build time.
  return {
    mode: resolveAuthMode(process.env.NEXT_PUBLIC_AUTH_MODE, process.env.NODE_ENV),
    firebaseApiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || undefined,
    tenantId: process.env.NEXT_PUBLIC_FIREBASE_TENANT_ID || undefined,
  };
}
