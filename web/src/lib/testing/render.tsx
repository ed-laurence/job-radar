import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { ApiProvider, createJobRadarClient } from "@/lib/api";
import { type AuthAdapter, AuthProvider, createFakeAuth } from "@/lib/auth";
import { API, OWNER } from "./http";

export interface RenderWithApiOptions {
  /** Fake account to sign in as (default: the maintainer). Ignored when `adapter` is given. */
  email?: string;
  /** A ready-made auth adapter, for tests that drive sign-in themselves. */
  adapter?: AuthAdapter;
}

/** A fake-mode adapter already signed in as `email`, like a reload after sign-in. */
export function signedInAuth(email: string = OWNER): AuthAdapter {
  const storage = window.sessionStorage;
  storage.setItem("jr.fakeAuth.email", email);
  return createFakeAuth(storage);
}

/** Renders `ui` inside the auth and API providers the app uses, against an MSW-mocked API. */
export function renderWithApi(ui: ReactElement, options: RenderWithApiOptions = {}) {
  const adapter = options.adapter ?? signedInAuth(options.email);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const client = createJobRadarClient({ baseUrl: API, getToken: () => adapter.getToken() });
  const view = render(
    <AuthProvider adapter={adapter}>
      <ApiProvider client={client} queryClient={queryClient}>
        {ui}
      </ApiProvider>
    </AuthProvider>,
  );
  return { ...view, queryClient };
}
