import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { createJobRadarClient } from "./client";
import { toApiError } from "./errors";
import { ApiProvider, shouldRetry, useApiClient } from "./query";

describe("shouldRetry", () => {
  it("never retries client errors", () => {
    const err = toApiError(404, null, null);
    expect(shouldRetry(0, err)).toBe(false);
  });

  it("retries server and network errors up to the limit", () => {
    expect(shouldRetry(0, toApiError(503, null, null))).toBe(true);
    expect(shouldRetry(1, new TypeError("network"))).toBe(true);
    expect(shouldRetry(2, new TypeError("network"))).toBe(false);
  });
});

describe("useApiClient", () => {
  it("returns the provided client", () => {
    const client = createJobRadarClient({ getToken: async () => null });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApiProvider client={client}>{children}</ApiProvider>
    );
    const { result } = renderHook(() => useApiClient(), { wrapper });
    expect(result.current).toBe(client);
  });
});
