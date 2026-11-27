/**
 * The Roles list's age filter. Age is days since `posted_at` (or `found_at`); the API takes
 * `min_age_days` / `max_age_days`, and the URL uses the same names so a reload keeps the filter.
 */

export interface AgeRange {
  min: number | "";
  max: number | "";
}

export const ANY_AGE: AgeRange = { min: "", max: "" };
export const AGE_MAX_DAYS = 3650;
export const CUSTOM_AGE = "custom";

export const AGE_PRESETS: readonly { value: string; label: string; range: AgeRange }[] = [
  { value: "any", label: "Any", range: ANY_AGE },
  { value: "1", label: "Last 24 hours", range: { min: "", max: 1 } },
  { value: "3", label: "Last 3 days", range: { min: "", max: 3 } },
  { value: "7", label: "Last 7 days", range: { min: "", max: 7 } },
  { value: "14", label: "Last 14 days", range: { min: "", max: 14 } },
  { value: "30", label: "Last 30 days", range: { min: "", max: 30 } },
  { value: "older30", label: "Older than 30 days", range: { min: 30, max: "" } },
];

/** The preset matching a range, or "custom". */
export function presetOf(r: AgeRange): string {
  return (
    AGE_PRESETS.find((p) => p.range.min === r.min && p.range.max === r.max)?.value ?? CUSTOM_AGE
  );
}

export function rangeOf(preset: string): AgeRange | undefined {
  return AGE_PRESETS.find((p) => p.value === preset)?.range;
}

function day(raw: string | string[] | null | undefined): number | "" {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (v === undefined || v === null || !/^\d{1,4}$/.test(v)) return "";
  const n = Number(v);
  return n <= AGE_MAX_DAYS ? n : "";
}

/** Reads the range from page search params or a URLSearchParams; anything malformed is ignored. */
export function parseAge(
  params: URLSearchParams | Record<string, string | string[] | undefined>,
): AgeRange {
  const get = (k: string) => (params instanceof URLSearchParams ? params.get(k) : params[k]);
  return { min: day(get("min_age_days")), max: day(get("max_age_days")) };
}

/** Why the range can't be sent, or null. */
export function ageError(r: AgeRange): string | null {
  if (r.min !== "" && r.max !== "" && r.min > r.max) {
    return "The 'from' age must be no more than the 'to' age.";
  }
  return null;
}

/** The listRoles query parameters for a valid range (nothing for an invalid one). */
export function ageQuery(r: AgeRange): { min_age_days?: number; max_age_days?: number } {
  if (ageError(r)) return {};
  return {
    ...(r.min !== "" ? { min_age_days: r.min } : {}),
    ...(r.max !== "" ? { max_age_days: r.max } : {}),
  };
}

/** `search` (e.g. `?a=1`) with the age parameters replaced; other parameters are kept. */
export function withAgeParams(search: string, r: AgeRange): string {
  const params = new URLSearchParams(search);
  params.delete("min_age_days");
  params.delete("max_age_days");
  if (r.min !== "") params.set("min_age_days", String(r.min));
  if (r.max !== "") params.set("max_age_days", String(r.max));
  const out = params.toString();
  return out ? `?${out}` : "";
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** Whole days since the role was posted (or found, when the posting date is unknown). */
export function ageDays(
  role: { posted_at?: string | null; found_at: string },
  now: number = Date.now(),
): number | null {
  const at = Date.parse(role.posted_at || role.found_at);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.floor((now - at) / DAY_MS));
}

/** The Roles table's Age cell: "Today", "1 day", "12 days", or a dash when unknown. */
export function ageLabel(
  role: { posted_at?: string | null; found_at: string },
  now: number = Date.now(),
): string {
  const days = ageDays(role, now);
  if (days === null) return "—";
  if (days === 0) return "Today";
  return days === 1 ? "1 day" : `${days} days`;
}
