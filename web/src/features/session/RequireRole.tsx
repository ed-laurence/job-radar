"use client";

import type { ReactNode } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import type { Schemas } from "@/lib/api";
import { useMe } from "@/lib/session";

export interface RequireRoleProps {
  requires: Schemas["AppRole"];
  children: ReactNode;
}

/** Page-level role gate (UX only; the API enforces roles on every request). */
export function RequireRole({ requires, children }: RequireRoleProps) {
  const me = useMe();
  if (!me.data) return <LoadingState />;
  if (requires === "maintainer" && me.data.role !== "maintainer") {
    return <ErrorState title="You don't have access to this page" />;
  }
  return <>{children}</>;
}
