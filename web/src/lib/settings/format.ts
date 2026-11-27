/** True when `zone` is an IANA time zone this browser knows. */
export function isValidTimeZone(zone: string): boolean {
  if (!zone.trim()) return false;
  try {
    new Intl.DateTimeFormat("en-AU", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/**
 * A date and time for display, in the user's chosen zone when it is valid (else the browser's).
 * Returns an empty string for a value that isn't a date.
 */
export function formatDateTime(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const zone = timeZone && isValidTimeZone(timeZone) ? { timeZone } : {};
  return new Intl.DateTimeFormat("en-AU", {
    dateStyle: "medium",
    timeStyle: "short",
    ...zone,
  }).format(date);
}

/**
 * A short weekday date and time, like "Thu, 26 Nov, 7:02 am", in the user's chosen zone when it
 * is valid (else the browser's). Returns an empty string for a value that isn't a date.
 */
export function formatShortDateTime(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const zone = timeZone && isValidTimeZone(timeZone) ? { timeZone } : {};
  const parts = new Intl.DateTimeFormat("en-AU", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    ...zone,
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? "";
  return `${part("weekday")}, ${part("day")} ${part("month")}, ${part("hour")}:${part("minute")} ${part("dayPeriod").toLowerCase()}`;
}
