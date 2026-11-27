import { describe, expect, it, vi } from "vitest";
import { resolveAuthMode } from "./config";
import { createFakeAuth, FAKE_USERS, fakeToken } from "./fake";

describe("resolveAuthMode", () => {
  it.each([
    ["fake", "production", "fake"],
    ["identity", "development", "identity"],
    [undefined, "development", "fake"],
    [undefined, "test", "fake"],
    [undefined, "production", "identity"],
    ["bogus", "production", "identity"],
  ] as const)("%s in %s -> %s", (raw, env, mode) => {
    expect(resolveAuthMode(raw, env)).toBe(mode);
  });
});

describe("fake auth", () => {
  it("offers exactly the two placeholder accounts", () => {
    expect(FAKE_USERS.map((u) => u.email)).toEqual(["owner@example.test", "tester@example.test"]);
  });

  it("signs in a placeholder user and sends the fake bearer token", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    const listener = vi.fn();
    auth.subscribe(listener);
    expect(listener).toHaveBeenLastCalledWith(null);
    expect(await auth.signInAs("tester@example.test")).toEqual({ kind: "signed-in" });
    expect(listener).toHaveBeenLastCalledWith({ email: "tester@example.test" });
    expect(await auth.getToken()).toBe(fakeToken("tester@example.test"));
    expect(fakeToken("tester@example.test")).toBe("fake:tester@example.test");
  });

  it("refuses anyone else", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    expect(await auth.signInAs("someone@example.test")).toEqual({ kind: "refused" });
    expect(await auth.signInWithPassword("owner@example.test", "pw")).toEqual({ kind: "refused" });
    expect(await auth.getToken()).toBeNull();
  });

  it("restores the session from storage and signs out", async () => {
    await createFakeAuth(window.sessionStorage).signInAs("owner@example.test");
    const again = createFakeAuth(window.sessionStorage);
    expect(await again.getToken()).toBe("fake:owner@example.test");
    await again.signOut();
    expect(await createFakeAuth(window.sessionStorage).getToken()).toBeNull();
  });

  it("treats every email-action code as invalid (local accounts get no links)", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    expect(await auth.checkPasswordResetCode("c")).toEqual({ kind: "invalid" });
    expect(await auth.confirmPasswordReset("c", "a-long-new-password")).toEqual({
      kind: "invalid",
    });
    expect(await auth.applyEmailAction("verifyEmail", "c")).toEqual({ kind: "invalid" });
  });
});
