"use client";

import type { ReactNode } from "react";
import { HealthMenu } from "@/features/health";
import { ScanStatusBar } from "@/features/scan";
import { AppFrame } from "@/features/session";

/**
 * Composes the signed-in frame with the scan line and the Health menu (features don't import
 * each other).
 */
export function SignedInFrame({ children }: { children: ReactNode }) {
  return (
    <AppFrame
      actions={(me) => (
        <>
          <ScanStatusBar maintainer={me.role === "maintainer"} />
          <HealthMenu maintainer={me.role === "maintainer"} />
        </>
      )}
    >
      {children}
    </AppFrame>
  );
}
