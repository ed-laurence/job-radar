"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback } from "react";
import { AppShell } from "@/components/AppShell";
import { AnalyticsProvider } from "@/lib/AnalyticsProvider";
import type { WebEvent } from "@/lib/analytics";
import { unwrap, useApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Me } from "@/lib/session";
import { navFor } from "./nav";
import { RequireAuth } from "./RequireAuth";

export interface AppFrameProps {
  children: ReactNode;
  /** Top-bar controls for the signed-in account, composed by the app (e.g. the Health menu). */
  actions?: (me: Me) => ReactNode;
}

/** The signed-in app: route guard, shell, nav for the account's role, and web analytics. */
export function AppFrame({ children, actions }: AppFrameProps) {
  const { adapter } = useAuth();
  const client = useApiClient();
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const queryClient = useQueryClient();

  const sendEvents = useCallback(
    async (events: WebEvent[], { keepalive }: { keepalive: boolean }) => {
      await unwrap(client.POST("/events", { body: { events }, keepalive }));
    },
    [client],
  );

  const signOut = useCallback(async () => {
    await adapter.signOut();
    queryClient.clear();
    router.replace("/sign-in");
  }, [adapter, queryClient, router]);

  return (
    <RequireAuth>
      {(me) => (
        <AnalyticsProvider send={sendEvents}>
          <AppShell
            navItems={navFor(me.role)}
            currentPath={pathname}
            accountLabel={me.email}
            accountDetail={me.role === "maintainer" ? "Maintainer" : "User"}
            actions={actions?.(me)}
            onSignOut={() => void signOut()}
          >
            {children}
          </AppShell>
        </AnalyticsProvider>
      )}
    </RequireAuth>
  );
}
