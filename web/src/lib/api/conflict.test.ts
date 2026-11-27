import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { handleVersionConflict, retryOnVersionConflict } from "./conflict";
import { toApiError } from "./errors";

const conflict = (current: number) =>
  toApiError(
    409,
    {
      type: "/problems/version-conflict",
      title: "Version conflict",
      status: 409,
      correlation_id: "c",
      current_version: current,
    },
    null,
  );

describe("handleVersionConflict", () => {
  it("invalidates the stale query and reports the conflict", async () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const handled = await handleVersionConflict(conflict(5), queryClient, ["roles"]);
    expect(handled).toBe(true);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["roles"] });
  });

  it("ignores other errors", async () => {
    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    expect(await handleVersionConflict(new Error("x"), queryClient, ["roles"])).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("retryOnVersionConflict", () => {
  it("retries once with the server's current version", async () => {
    const attempt = vi
      .fn<(version: number) => Promise<string>>()
      .mockRejectedValueOnce(conflict(7))
      .mockResolvedValueOnce("ok");
    await expect(retryOnVersionConflict(attempt, 6)).resolves.toBe("ok");
    expect(attempt.mock.calls).toEqual([[6], [7]]);
  });

  it("gives up after the retry budget and rethrows the conflict", async () => {
    const attempt = vi.fn<(version: number) => Promise<string>>().mockRejectedValue(conflict(9));
    await expect(retryOnVersionConflict(attempt, 1, { maxRetries: 1 })).rejects.toMatchObject({
      status: 409,
    });
    expect(attempt).toHaveBeenCalledTimes(2);
  });

  it("does not retry non-conflict errors", async () => {
    const attempt = vi
      .fn<(version: number) => Promise<string>>()
      .mockRejectedValue(new Error("boom"));
    await expect(retryOnVersionConflict(attempt, 1)).rejects.toThrow("boom");
    expect(attempt).toHaveBeenCalledTimes(1);
  });
});
