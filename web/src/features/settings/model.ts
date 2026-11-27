import type { Schemas } from "@/lib/api";
import { isValidTimeZone } from "@/lib/settings";

export type Settings = Schemas["UserSettings"];
export type SettingsUpdate = Schemas["UserSettingsUpdate"];
export type Spelling = Settings["kits"]["spelling"];
export type ScoringPreferences = Schemas["ScoringPreferences"];
export type WorkMode = Schemas["WorkMode"];
export type WeightField =
  | "weight_skills"
  | "weight_track_pay"
  | "weight_location"
  | "weight_freshness";
export type PayField = "perm_salary_aud" | "contract_day_rate_aud";

export const TEXT_MAX = 8000;

export const MAX_LOCATIONS = 10;
export const LOCATION_MAX = 100;

export const WORK_MODES: readonly { value: WorkMode; label: string }[] = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "on_site", label: "On-site" },
];

export const WEIGHT_FIELDS: readonly WeightField[] = [
  "weight_skills",
  "weight_track_pay",
  "weight_location",
  "weight_freshness",
];

export const PAY_RULES: Readonly<Record<PayField, { max: number; label: string }>> = {
  perm_salary_aud: { max: 2_000_000, label: "Permanent salary floor" },
  contract_day_rate_aud: { max: 20_000, label: "Contract day rate floor" },
};

export const SPELLINGS: readonly { value: Spelling; label: string }[] = [
  { value: "en-AU", label: "Australian English" },
  { value: "en-GB", label: "British English" },
  { value: "en-US", label: "American English" },
];

/** Offered in the time zone select; any other IANA zone can be typed in. */
export const COMMON_ZONES: readonly string[] = [
  "Australia/Sydney",
  "Australia/Melbourne",
  "Australia/Brisbane",
  "Australia/Adelaide",
  "Australia/Perth",
  "Australia/Hobart",
  "Australia/Darwin",
  "Pacific/Auckland",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Asia/Kolkata",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "UTC",
];

/** The form's values. Numbers are kept as typed so a field can be blank while editing. */
export interface Draft {
  skills: string;
  brief: string;
  keep_score: string;
  borderline_score: string;
  notify_score: string;
  notify_mission_score: string;
  weight_skills: string;
  weight_track_pay: string;
  weight_location: string;
  weight_freshness: string;
  perm_salary_aud: string;
  contract_day_rate_aud: string;
  work_modes: WorkMode[];
  preferred_locations: string[];
  after_hours: string;
  due_hours: string;
  nudge_days: string;
  auto_kits: boolean;
  kits_per_run: string;
  spelling: Spelling;
  start_date: string;
  archive_default_days: string;
  monthly_budget_aud: string;
  timezone: string;
}

export type DraftField = keyof Draft;
/** `weights` is the "must total 100" check, shown on the weights group. */
export type Errors = Partial<Record<DraftField | "weights", string>>;

const str = (n: number | null | undefined) =>
  typeof n === "number" && Number.isFinite(n) ? String(n) : "";

/** The stored budget; null (no cap) when absent, so a partial response never breaks the page. */
const storedBudget = (s: Settings): number | null => {
  const n = (s.llm as Partial<Settings["llm"]> | undefined)?.monthly_budget_aud;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
};

export function toDraft(s: Settings): Draft {
  return {
    skills: s.profile.skills,
    brief: s.profile.brief,
    keep_score: str(s.scoring.keep_score),
    borderline_score: str(s.scoring.borderline_score),
    notify_score: str(s.scoring.notify_score),
    notify_mission_score: str(s.scoring.notify_mission_score),
    weight_skills: str(s.scoring_preferences.weights.skills),
    weight_track_pay: str(s.scoring_preferences.weights.track_pay),
    weight_location: str(s.scoring_preferences.weights.location),
    weight_freshness: str(s.scoring_preferences.weights.freshness),
    perm_salary_aud: str(s.scoring_preferences.pay_floors.perm_salary_aud),
    contract_day_rate_aud: str(s.scoring_preferences.pay_floors.contract_day_rate_aud),
    work_modes: [...s.scoring_preferences.work_modes],
    preferred_locations: [...s.scoring_preferences.preferred_locations],
    after_hours: str(s.followups.after_hours),
    due_hours: str(s.followups.due_hours),
    nudge_days: str(s.followups.nudge_days),
    auto_kits: s.kits.auto_kits,
    kits_per_run: str(s.kits.kits_per_run),
    spelling: s.kits.spelling,
    start_date: s.campaign.start_date ?? "",
    archive_default_days: str(s.archive_default_days),
    monthly_budget_aud: str(storedBudget(s)),
    timezone: s.display.timezone,
  };
}

export interface IntRule {
  min: number;
  max: number;
  label: string;
}

export type IntField =
  | "keep_score"
  | "borderline_score"
  | "notify_score"
  | "notify_mission_score"
  | WeightField
  | "after_hours"
  | "due_hours"
  | "nudge_days"
  | "kits_per_run"
  | "archive_default_days";

export const INT_RULES: Readonly<Record<IntField, IntRule>> = {
  keep_score: { min: 0, max: 100, label: "Keep score" },
  borderline_score: { min: 0, max: 100, label: "Borderline score" },
  notify_score: { min: 0, max: 100, label: "Notify score" },
  notify_mission_score: { min: 0, max: 100, label: "Mission notify score" },
  weight_skills: { min: 0, max: 100, label: "Skills weight" },
  weight_track_pay: { min: 0, max: 100, label: "Track and pay weight" },
  weight_location: { min: 0, max: 100, label: "Location weight" },
  weight_freshness: { min: 0, max: 100, label: "Freshness weight" },
  after_hours: { min: 1, max: 336, label: "Suggest a follow-up after" },
  due_hours: { min: 1, max: 720, label: "Follow-up due after" },
  nudge_days: { min: 1, max: 60, label: "Remind again after" },
  kits_per_run: { min: 0, max: 50, label: "Kits per run" },
  archive_default_days: { min: 1, max: 365, label: "Archive older than" },
};

const INT = /^\d+$/;

/** The fields in form order, so the first error can take focus. */
export const FIELD_ORDER: readonly DraftField[] = [
  "skills",
  "brief",
  "keep_score",
  "borderline_score",
  "notify_score",
  "notify_mission_score",
  "weight_skills",
  "weight_track_pay",
  "weight_location",
  "weight_freshness",
  "perm_salary_aud",
  "contract_day_rate_aud",
  "work_modes",
  "preferred_locations",
  "after_hours",
  "due_hours",
  "nudge_days",
  "auto_kits",
  "kits_per_run",
  "spelling",
  "start_date",
  "archive_default_days",
  "timezone",
  "monthly_budget_aud",
];

/** Field errors. Empty when the draft can be saved. */
export function validate(d: Draft, { maintainer }: { maintainer: boolean }): Errors {
  const errors: Errors = {};
  if (d.skills.length > TEXT_MAX) errors.skills = `Keep it to ${TEXT_MAX} characters.`;
  if (d.brief.length > TEXT_MAX) errors.brief = `Keep it to ${TEXT_MAX} characters.`;
  for (const [field, rule] of Object.entries(INT_RULES) as [IntField, IntRule][]) {
    const raw = d[field].trim();
    const n = Number(raw);
    if (!INT.test(raw) || n < rule.min || n > rule.max) {
      errors[field] = `${rule.label} must be a whole number from ${rule.min} to ${rule.max}.`;
    }
  }
  if (
    !errors.keep_score &&
    !errors.borderline_score &&
    Number(d.keep_score) < Number(d.borderline_score)
  ) {
    errors.keep_score = "Keep score must be at least the borderline score.";
  }
  if (WEIGHT_FIELDS.every((f) => !errors[f])) {
    const total = weightTotal(d);
    if (total !== 100) errors.weights = `Weights must total 100; they total ${total} now.`;
  }
  for (const [field, rule] of Object.entries(PAY_RULES) as [
    PayField,
    { max: number; label: string },
  ][]) {
    const raw = d[field].trim();
    if (raw && (!INT.test(raw) || Number(raw) > rule.max)) {
      errors[field] =
        `${rule.label} must be a whole number from 0 to ${rule.max.toLocaleString("en-AU")}, or blank for none.`;
    }
  }
  if (d.preferred_locations.length > MAX_LOCATIONS) {
    errors.preferred_locations = `Keep it to ${MAX_LOCATIONS} locations.`;
  } else if (d.preferred_locations.some((l) => !l.trim() || l.length > LOCATION_MAX)) {
    errors.preferred_locations = `Each location must be 1 to ${LOCATION_MAX} characters.`;
  }
  if (d.start_date && !/^\d{4}-\d{2}-\d{2}$/.test(d.start_date)) {
    errors.start_date = "Use a date like 2026-11-02, or leave it blank.";
  }
  if (maintainer && d.monthly_budget_aud.trim()) {
    const n = Number(d.monthly_budget_aud);
    if (!Number.isFinite(n) || n < 0 || n > 10_000) {
      errors.monthly_budget_aud = "Budget must be from 0 to 10000 AUD, or blank for no cap.";
    }
  }
  if (d.timezone.length > 64 || !isValidTimeZone(d.timezone.trim())) {
    errors.timezone = "Enter an IANA time zone, such as Australia/Melbourne.";
  }
  return errors;
}

/** The weights' sum; blank or non-numeric weights count as 0. */
export function weightTotal(d: Pick<Draft, WeightField>): number {
  return WEIGHT_FIELDS.reduce((sum, f) => {
    const n = Number(d[f].trim());
    return sum + (Number.isFinite(n) ? n : 0);
  }, 0);
}

/** The fields whose values differ between two drafts (after a version conflict). */
export function changedFields(before: Draft, after: Draft): DraftField[] {
  return FIELD_ORDER.filter((f) => JSON.stringify(before[f]) !== JSON.stringify(after[f]));
}

const floor = (raw: string) => (raw.trim() ? Number(raw.trim()) : null);

function budgetOf(d: Draft, base: Settings, maintainer: boolean): number | null {
  if (!maintainer) return storedBudget(base);
  const raw = d.monthly_budget_aud.trim();
  return raw ? Number(raw) : null;
}

/**
 * The PUT body: the draft against `base` (the version last read). Only maintainers can change
 * the LLM budget, so everyone else sends the stored value back unchanged.
 */
export function toUpdate(
  d: Draft,
  base: Settings,
  { maintainer }: { maintainer: boolean },
): SettingsUpdate {
  return {
    profile: { skills: d.skills, brief: d.brief },
    scoring: {
      keep_score: Number(d.keep_score),
      borderline_score: Number(d.borderline_score),
      notify_score: Number(d.notify_score),
      notify_mission_score: Number(d.notify_mission_score),
    },
    scoring_preferences: {
      weights: {
        skills: Number(d.weight_skills),
        track_pay: Number(d.weight_track_pay),
        location: Number(d.weight_location),
        freshness: Number(d.weight_freshness),
      },
      pay_floors: {
        perm_salary_aud: floor(d.perm_salary_aud),
        contract_day_rate_aud: floor(d.contract_day_rate_aud),
      },
      work_modes: WORK_MODES.map((m) => m.value).filter((m) => d.work_modes.includes(m)),
      preferred_locations: d.preferred_locations.map((l) => l.trim()),
    },
    followups: {
      after_hours: Number(d.after_hours),
      due_hours: Number(d.due_hours),
      nudge_days: Number(d.nudge_days),
    },
    kits: {
      auto_kits: d.auto_kits,
      kits_per_run: Number(d.kits_per_run),
      spelling: d.spelling,
    },
    campaign: { start_date: d.start_date || null },
    archive_default_days: Number(d.archive_default_days),
    llm: { monthly_budget_aud: budgetOf(d, base, maintainer) },
    display: { timezone: d.timezone.trim() },
    version: base.version,
  };
}
