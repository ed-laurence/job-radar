/** Rank digits in ascending byte order, matching the server's `COLLATE "C"` sort. */
export const RANK_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const BASE = RANK_DIGITS.length;
const MAX_LENGTH = 64;

/**
 * A rank key strictly between `before` and `after` in byte order (`null` = open end).
 * Returns null when none exists (e.g. between `a` and `a0`) or the key would exceed the spec's
 * 64 characters; the caller then lets the server place the card.
 */
export function rankBetween(before: string | null, after: string | null): string | null {
  const a = before ?? "";
  let b = after;
  if (b !== null && a >= b) return null;
  let prefix = "";
  for (let i = 0; i < MAX_LENGTH; i++) {
    const da = i < a.length ? RANK_DIGITS.indexOf(a.charAt(i)) : -1;
    const db = b !== null && i < b.length ? RANK_DIGITS.indexOf(b.charAt(i)) : BASE;
    if (da === db) {
      prefix += a.charAt(i);
      continue;
    }
    if (db - da > 1) {
      const mid = Math.floor((da + db) / 2);
      if (mid > 0) return prefix + RANK_DIGITS.charAt(mid);
      // Never end a key on the smallest digit, or nothing could ever go before it.
      prefix += RANK_DIGITS.charAt(0);
      b = null;
      continue;
    }
    if (da === -1) {
      // `a` is exhausted and b's next digit is the smallest one: descend under it.
      if (b === null || i + 1 >= b.length) return null;
      prefix += RANK_DIGITS.charAt(0);
      continue;
    }
    // Adjacent digits: keep a's digit; anything after it that beats a's tail is below `b`.
    prefix += a.charAt(i);
    b = null;
  }
  return null;
}
