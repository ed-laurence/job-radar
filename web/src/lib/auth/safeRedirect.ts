const DEFAULT_DESTINATION = "/dashboard";

/**
 * The `next` parameter after sign-in, accepted only as a same-origin path.
 * Rejects absolute URLs, protocol-relative `//host` and backslash tricks (open redirects).
 */
export function safeRedirectPath(next: string | null | undefined): string {
  if (!next?.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return DEFAULT_DESTINATION;
  }
  const hasControlChar = [...next].some((ch) => ch.charCodeAt(0) < 0x20);
  if (hasControlChar || next.startsWith("/sign-in")) return DEFAULT_DESTINATION;
  return next;
}
