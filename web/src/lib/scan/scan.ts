import type { Schemas } from "@/lib/api";

export type ScanRun = Schemas["ScanRun"];
export type ScanStatus = Schemas["ScanStatus"];

/** The top-bar dot: green after a good scan, amber after a failed one or none at all. */
export type ScanDot = "ok" | "warn" | "unknown";

const count = new Intl.NumberFormat("en");

/** A short age, as on job-seeker: "3m", "2h 5m", "3d". */
export function ago(ms: number): string {
  if (!Number.isFinite(ms)) return "–";
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d`;
}

function time(iso: string | null | undefined): number {
  return iso ? Date.parse(iso) : Number.NaN;
}

/** "Last scan 3m ago · next in 12 min", "Scanning now…" or "No scans yet". */
export function scanLine(status: ScanStatus, now: number): string {
  if (status.running) return "Scanning now…";
  const parts: string[] = [];
  const last = status.last_run;
  const lastAt = time(last?.finished_at ?? last?.started_at);
  parts.push(Number.isFinite(lastAt) ? `Last scan ${ago(now - lastAt)} ago` : "No scans yet");
  const nextAt = time(status.next_run_at);
  if (Number.isFinite(nextAt)) {
    parts.push(`next in ${Math.max(0, Math.round((nextAt - now) / 60_000))} min`);
  }
  return parts.join(" · ");
}

export function scanDot(status: ScanStatus | undefined): ScanDot {
  if (!status) return "unknown";
  const s = status.last_run?.status;
  return s === "ok" || s === "no_new_roles" ? "ok" : s === "running" ? "unknown" : "warn";
}

/** What a dot colour means, for screen readers and the tooltip. */
export const SCAN_DOT_TEXT: Readonly<Record<ScanDot, string>> = {
  ok: "Last scan OK",
  warn: "Last scan failed or no scan has run",
  unknown: "Scan status unknown",
};

const roles = (n: number) => `${count.format(n)} new role${n === 1 ? "" : "s"}`;

/** The toast after Scan now: "5 new roles" or "No new roles". */
export function scanResultText(run: ScanRun): string {
  if (run.status === "failed") {
    return run.new > 0 ? `${roles(run.new)} (the scan stopped early)` : "The scan failed";
  }
  return run.new > 0 ? roles(run.new) : "No new roles";
}

export function poolText(remaining: number): string {
  return `Pool: ${count.format(remaining)} role${remaining === 1 ? "" : "s"} left`;
}
