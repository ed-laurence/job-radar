import type { Schemas } from "@/lib/api";

/** Display words for triage enums, so nothing is conveyed by colour alone. */
export const SEVERITY_LABEL: Record<Schemas["TaskSeverity"], string> = {
  critical: "Critical",
  major: "Major",
  minor: "Minor",
  trivial: "Trivial",
};

export const ROUTE_LABEL: Record<Schemas["TriageRoute"], string> = {
  now: "Now",
  next: "Next",
  later: "Later",
};

export const VERIFICATION_LABEL: Record<Schemas["TriageVerification"]["status"], string> = {
  confirmed: "Confirmed",
  not_reproduced: "Not reproduced",
  unclear: "Unclear",
  duplicate: "Duplicate",
};
