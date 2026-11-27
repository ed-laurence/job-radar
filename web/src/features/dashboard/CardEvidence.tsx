"use client";

import { useState } from "react";
import { EvidenceBlock } from "@/components/EvidenceBlock";
import type { Schemas } from "@/lib/api";
import { useEvidence } from "@/lib/evidence";

/** The card's evidence strip: the summary line, with the full test loaded when it's opened. */
export function CardEvidence({ role }: { role: Schemas["Role"] }) {
  const [opened, setOpened] = useState(false);
  const evidence = useEvidence(role.id, opened);
  return (
    <EvidenceBlock
      variant="card"
      summary={role.evidence}
      onToggle={(open) => {
        if (open) setOpened(true);
      }}
      {...evidence}
    />
  );
}
