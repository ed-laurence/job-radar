"use client";

import { type ReactNode, useState } from "react";
import { ApiProvider, createJobRadarClient, createQueryClient } from "@/lib/api";
import { AuthProvider, createAuthAdapter, readAuthConfig } from "@/lib/auth";

/** Wires auth into the API client: every request carries the current account's token. */
export function Providers({ children }: { children: ReactNode }) {
  const [adapter] = useState(() => createAuthAdapter(readAuthConfig()));
  const [client] = useState(() => createJobRadarClient({ getToken: () => adapter.getToken() }));
  const [queryClient] = useState(createQueryClient);
  return (
    <AuthProvider adapter={adapter}>
      <ApiProvider client={client} queryClient={queryClient}>
        {children}
      </ApiProvider>
    </AuthProvider>
  );
}
