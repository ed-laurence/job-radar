import type { Schemas } from "@/lib/api";

export type Role = Schemas["Role"];
export type RoleStatus = Schemas["RoleStatus"];
export type RoleStage = Schemas["RoleStage"];
export type Track = Schemas["Track"];
export type WorkMode = Schemas["WorkMode"];
export type RejectionCategory = Schemas["RejectionCategory"];

export const STATUS_LABELS: Record<RoleStatus, string> = {
  new: "New",
  reviewing: "Reviewing",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  passed: "Passed",
  closed: "Closed",
  rejected: "Rejected",
  unknown: "Unknown",
};
export const STATUSES = Object.keys(STATUS_LABELS) as RoleStatus[];

export const TRACK_LABELS: Record<Track, string> = {
  contract: "Contract",
  exec: "Exec",
  mission: "Mission",
};
export const TRACKS = Object.keys(TRACK_LABELS) as Track[];

export const WORK_MODE_LABELS: Record<WorkMode, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  on_site: "On site",
};
export const WORK_MODES = Object.keys(WORK_MODE_LABELS) as WorkMode[];

export const REJECTION_LABELS: Record<RejectionCategory, string> = {
  skills_gap: "Skills gap",
  experience_level: "Experience level",
  seniority: "Seniority",
  location: "Location",
  pay: "Pay",
  visa: "Visa",
  internal_candidate: "Internal candidate",
  role_filled: "Role filled",
  stronger_candidate: "Stronger candidate",
  culture_fit: "Culture fit",
  no_reason_given: "No reason given",
  other: "Other",
};
export const REJECTION_CATEGORIES = Object.keys(REJECTION_LABELS) as RejectionCategory[];

export const SORTS = {
  rank: "Pipeline order",
  "-found_at": "Newest found",
  "-score": "Highest score",
  "-posted_at": "Newest posted",
} as const;
export type RoleSort = keyof typeof SORTS;

export function formatDate(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 10);
}
