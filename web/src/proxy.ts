import { type NextRequest, NextResponse } from "next/server";
import { resolveAuthMode } from "./lib/auth/config";
import { buildCsp } from "./lib/csp";

/** Per-request CSP nonce (Next 16 "proxy", formerly middleware). Next stamps it on its scripts. */
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const csp = buildCsp({
    nonce,
    dev: process.env.NODE_ENV === "development",
    authMode: resolveAuthMode(process.env.NEXT_PUBLIC_AUTH_MODE, process.env.NODE_ENV),
  });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Pages only: the BFF routes and static assets don't render HTML.
      source: "/((?!api/|hooks/|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
