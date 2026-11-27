import type { Schemas } from "@/lib/api";
import { isActNow } from "./tabs";

type Role = Schemas["Role"];

const HOUR = 3_600_000;

export type ScoreBand = "high" | "mid" | "low" | "none";

/** Score badge colour band, as on job-seeker: 75 and up, 55 and up, lower. */
export function scoreBand(score: number | undefined): ScoreBand {
  if (score === undefined) return "none";
  return score >= 75 ? "high" : score >= 55 ? "mid" : "low";
}

export type Heat = "hot" | "warm" | "cold";

/** Under an hour since posting is hot, under four hours warm. */
export function heat(ageMs: number): Heat {
  return ageMs < HOUR ? "hot" : ageMs < 4 * HOUR ? "warm" : "cold";
}

/** A short age, as on job-seeker: "25m", "3h 12m", "2d". */
export function ago(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** "Posted 3h ago" (or "Found …" when the posting time is unknown), with its heat. */
export function clock(role: Role, now: number): { text: string; heat: Heat } {
  const at = Date.parse(role.posted_at ?? role.found_at);
  if (Number.isNaN(at)) return { text: "", heat: "cold" };
  const age = now - at;
  return { text: `${role.posted_at ? "Posted" : "Found"} ${ago(age)} ago`, heat: heat(age) };
}

/** The 60-minute response target: how much of it has gone, and what's left ("25m left", "past 60m"). */
export function countdown(role: Role, now: number): { pct: number; left: string } | undefined {
  const at = Date.parse(role.posted_at ?? role.found_at);
  if (Number.isNaN(at)) return undefined;
  const age = Math.max(0, now - at);
  return {
    pct: Math.min(100, (age / HOUR) * 100),
    left: age < HOUR ? `${ago(HOUR - age)} left` : "past 60m",
  };
}

/** "applied 3h after posting", for roles past Act now. */
export function appliedAfter(role: Role): string | undefined {
  if (!role.applied_at) return undefined;
  const at = Date.parse(role.posted_at ?? role.found_at);
  const applied = Date.parse(role.applied_at);
  if (Number.isNaN(at) || Number.isNaN(applied)) return undefined;
  return `applied ${ago(applied - at)} after posting`;
}

export type CardAction = "applied" | "passed" | "new";

/** Status buttons for a card: Mark applied before applying, Pass while acting, else back to Act now. */
export function cardActions(status: Schemas["RoleStatus"]): CardAction[] {
  const actions: CardAction[] = [];
  if (isActNow(status) || status === "passed" || status === "closed") actions.push("applied");
  actions.push(isActNow(status) ? "passed" : "new");
  return actions;
}

export const ACTION_LABELS: Readonly<Record<CardAction, string>> = {
  applied: "Mark applied",
  passed: "Pass",
  new: "Back to Act now",
};

export const ACTION_DONE: Readonly<Record<CardAction, string>> = {
  applied: "marked applied",
  passed: "passed",
  new: "moved back to Act now",
};
