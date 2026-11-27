import type { AuthMode } from "./auth/config";

/** Firebase Auth email/password + TOTP calls these from the browser; nothing else is allowed. */
export const IDENTITY_PLATFORM_ORIGINS = [
  "https://identitytoolkit.googleapis.com",
  "https://securetoken.googleapis.com",
] as const;

export interface CspOptions {
  nonce: string;
  dev: boolean;
  authMode: AuthMode;
}

export function buildCsp({ nonce, dev, authMode }: CspOptions): string {
  const connect = ["'self'", ...(authMode === "identity" ? IDENTITY_PLATFORM_ORIGINS : [])];
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": [
      "'self'",
      `'nonce-${nonce}'`,
      "'strict-dynamic'",
      ...(dev ? ["'unsafe-eval'"] : []),
    ],
    "style-src": ["'self'", `'nonce-${nonce}'`],
    // Style attributes carry dynamic values only (CODE-24), e.g. chart sizes.
    "style-src-attr": ["'unsafe-inline'"],
    "img-src": ["'self'", "data:", "blob:"],
    "font-src": ["'self'"],
    "connect-src": connect,
    "object-src": ["'none'"],
    "base-uri": ["'none'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
    "frame-src": ["'none'"],
    "worker-src": ["'self'"],
    "manifest-src": ["'self'"],
  };
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(" ")}`)
    .join("; ");
}

/** Static headers for every response (next.config). The CSP itself is per request (proxy.ts). */
export const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];
