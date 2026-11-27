"use client";

import { EvidenceBlock } from "@/components/EvidenceBlock";
import { useEvidence } from "@/lib/evidence";
import type { Role } from "./labels";

/** Role detail's evidence test, always expanded (the full test loads with the page). */
export function EvidencePanel({ role }: { role: Role }) {
  const evidence = useEvidence(role.id, true);
  return <EvidenceBlock variant="detail" summary={role.evidence} {...evidence} />;
}
