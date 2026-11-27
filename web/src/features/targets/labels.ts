import { enumLabels, type Schemas } from "@/lib/api";

export type TargetStatus = Schemas["TargetStatus"];
export type TargetLane = Schemas["TargetLane"];
export type TargetOrigin = Schemas["Target"]["origin"];

// The spec has no `x-enum-labels` for these yet; once it does, use `enumLabels` from lib/api.
export const STATUS_LABELS: Readonly<Record<TargetStatus, string>> = {
  suggested: "Suggested",
  approved: "Approved",
  dismissed: "Dismissed",
  approached: "Approached",
  replied: "Replied",
};
export const STATUSES = Object.keys(STATUS_LABELS) as TargetStatus[];

/** Lane names come from the spec's `x-enum-labels`. */
export const LANE_LABELS: Readonly<Record<TargetLane, string>> = enumLabels.TargetLane;
export const LANES = Object.keys(LANE_LABELS) as TargetLane[];

export const ORIGIN_LABELS: Readonly<Record<TargetOrigin, string>> = {
  ai: "AI suggested",
  import: "Imported",
  manual: "Manual",
};
