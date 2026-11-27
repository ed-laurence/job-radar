"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { type Me, useMe } from "@/lib/session";

export interface RequireAuthProps {
  children: (me: Me) => ReactNode;
}

function isRefusal(error: unknown): boolean {
  return isApiError(error) && (error.status === 401 || error.status === 403);
}

/**
 * Renders children only for a signed-in account the API accepts. Signed-out visitors go to
 * sign-in with a return path; an account the API refuses is signed out with the generic message.
 */
export function RequireAuth({ children }: RequireAuthProps) {
  const { status, adapter } = useAuth();
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const queryClient = useQueryClient();
  const me = useMe();
  const refused = me.isError && isRefusal(me.error);

  useEffect(() => {
    if (status === "signed-out") {
      router.replace(`/sign-in?next=${encodeURIComponent(pathname)}`);
    }
  }, [status, pathname, router]);

  useEffect(() => {
    if (!refused) return;
    void adapter.signOut().then(() => {
      queryClient.clear();
      router.replace("/sign-in?refused=1");
    });
  }, [refused, adapter, queryClient, router]);

  if (status !== "signed-in" || refused) return <LoadingState label="Checking your session…" />;
  if (me.isError) {
    return (
      <ErrorState
        title="Can't reach Job Radar"
        description="The service didn't respond. Try again in a moment."
        correlationId={isApiError(me.error) ? me.error.correlationId : undefined}
        onRetry={() => void me.refetch()}
      />
    );
  }
  if (!me.data) return <LoadingState label="Checking your session…" />;
  return <>{children(me.data)}</>;
}
