import { describe, expect, it } from "vitest";
import { buildCsp, IDENTITY_PLATFORM_ORIGINS, SECURITY_HEADERS } from "./csp";

function directives(csp: string): Map<string, string[]> {
  return new Map(
    csp
      .split(";")
      .map((d) => d.trim().split(/\s+/))
      .filter((parts) => parts[0])
      .map(([name, ...values]) => [name as string, values]),
  );
}

describe("buildCsp", () => {
  it("is strict in production: nonce + strict-dynamic, no eval, no inline scripts", () => {
    const d = directives(buildCsp({ nonce: "abc123", dev: false, authMode: "fake" }));
    expect(d.get("default-src")).toEqual(["'self'"]);
    expect(d.get("script-src")).toEqual(["'self'", "'nonce-abc123'", "'strict-dynamic'"]);
    expect(d.get("frame-ancestors")).toEqual(["'none'"]);
    expect(d.get("object-src")).toEqual(["'none'"]);
    expect(d.get("base-uri")).toEqual(["'none'"]);
    expect(d.get("form-action")).toEqual(["'self'"]);
    expect(d.get("connect-src")).toEqual(["'self'"]);
    expect(buildCsp({ nonce: "n", dev: false, authMode: "fake" })).not.toContain("unsafe-eval");
  });

  it("allows eval only in dev (React debugging)", () => {
    const d = directives(buildCsp({ nonce: "n", dev: true, authMode: "fake" }));
    expect(d.get("script-src")).toContain("'unsafe-eval'");
  });

  it("adds only the Identity Platform endpoints in identity mode", () => {
    const d = directives(buildCsp({ nonce: "n", dev: false, authMode: "identity" }));
    expect(d.get("connect-src")).toEqual(["'self'", ...IDENTITY_PLATFORM_ORIGINS]);
  });
});

describe("SECURITY_HEADERS", () => {
  it("sets HSTS, nosniff and denies framing", () => {
    const h = new Map(SECURITY_HEADERS.map(({ key, value }) => [key, value]));
    expect(h.get("Strict-Transport-Security")).toMatch(/max-age=\d{8,}; includeSubDomains/);
    expect(h.get("X-Content-Type-Options")).toBe("nosniff");
    expect(h.get("X-Frame-Options")).toBe("DENY");
    expect(h.get("Referrer-Policy")).toBe("no-referrer");
  });
});
