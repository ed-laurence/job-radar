import { beforeEach, describe, expect, it, vi } from "vitest";
import { createIdentityAuth } from "./identity";

// The Firebase SDK, mocked: no network and no real project.
const sdk = vi.hoisted(() => {
  const auth: {
    currentUser: null | {
      email: string;
      emailVerified: boolean;
      getIdToken: () => Promise<string>;
    };
    tenantId: string | null;
    authStateReady: () => Promise<void>;
  } = { currentUser: null, tenantId: null, authStateReady: async () => {} };
  return {
    auth,
    initializeApp: vi.fn(() => ({})),
    initializeAuth: vi.fn(() => auth),
    signInWithEmailAndPassword: vi.fn(),
    sendPasswordResetEmail: vi.fn(async () => {}),
    getMultiFactorResolver: vi.fn(),
    onIdTokenChanged: vi.fn(),
    signOut: vi.fn(async () => {
      auth.currentUser = null;
    }),
    assertionForSignIn: vi.fn(() => "assertion"),
    verifyPasswordResetCode: vi.fn(async (_auth: unknown, _code: string) => "owner@example.test"),
    confirmPasswordReset: vi.fn(async (_auth: unknown, _code: string, _pw: string) => {}),
    checkActionCode: vi.fn(async (_auth: unknown, _code: string) => ({
      operation: "RECOVER_EMAIL",
      data: { email: "owner@example.test" },
    })),
    applyActionCode: vi.fn(async (_auth: unknown, _code: string) => {}),
  };
});

vi.mock("@firebase/app", () => ({ initializeApp: sdk.initializeApp }));
vi.mock("@firebase/auth", () => ({
  initializeAuth: sdk.initializeAuth,
  browserSessionPersistence: "session",
  signInWithEmailAndPassword: sdk.signInWithEmailAndPassword,
  sendPasswordResetEmail: sdk.sendPasswordResetEmail,
  getMultiFactorResolver: sdk.getMultiFactorResolver,
  onIdTokenChanged: sdk.onIdTokenChanged,
  signOut: sdk.signOut,
  TotpMultiFactorGenerator: { FACTOR_ID: "totp", assertionForSignIn: sdk.assertionForSignIn },
  verifyPasswordResetCode: sdk.verifyPasswordResetCode,
  confirmPasswordReset: sdk.confirmPasswordReset,
  checkActionCode: sdk.checkActionCode,
  applyActionCode: sdk.applyActionCode,
}));

const CONFIG = { mode: "identity" as const, firebaseApiKey: "test-key", tenantId: undefined };
const user = (over: Partial<{ emailVerified: boolean; token: string }> = {}) => ({
  email: "owner@example.test",
  emailVerified: over.emailVerified ?? true,
  getIdToken: vi.fn(async () => over.token ?? "id-token-1"),
});

beforeEach(() => {
  vi.clearAllMocks();
  sdk.auth.currentUser = null;
  sdk.auth.authStateReady = async () => {};
});

describe("identity auth", () => {
  it("keeps the session in the tab and signs in with email and password", async () => {
    sdk.signInWithEmailAndPassword.mockImplementation(async () => {
      sdk.auth.currentUser = user();
    });
    const auth = createIdentityAuth(CONFIG);
    expect(await auth.signInWithPassword("owner@example.test", "pw")).toEqual({
      kind: "signed-in",
    });
    expect(sdk.initializeAuth).toHaveBeenCalledWith(expect.anything(), {
      persistence: "session",
    });
    expect(await auth.getToken()).toBe("id-token-1");
  });

  it("waits for the restored session before reading the token, and asks for a fresh one each time", async () => {
    let restore: (() => void) | undefined;
    sdk.auth.authStateReady = () =>
      new Promise<void>((resolve) => {
        restore = () => {
          sdk.auth.currentUser = u;
          resolve();
        };
      });
    const u = user();
    u.getIdToken.mockResolvedValueOnce("id-token-1").mockResolvedValueOnce("id-token-2");
    const auth = createIdentityAuth(CONFIG);
    const pending = auth.getToken();
    await vi.waitFor(() => expect(restore).toBeDefined());
    restore?.();
    expect(await pending).toBe("id-token-1");
    sdk.auth.authStateReady = async () => {};
    expect(await auth.getToken()).toBe("id-token-2");
    expect(u.getIdToken).toHaveBeenCalledTimes(2);
  });

  it("refuses an unverified email and signs it out", async () => {
    sdk.signInWithEmailAndPassword.mockImplementation(async () => {
      sdk.auth.currentUser = user({ emailVerified: false });
    });
    const auth = createIdentityAuth(CONFIG);
    expect(await auth.signInWithPassword("owner@example.test", "pw")).toEqual({
      kind: "refused",
    });
    expect(sdk.signOut).toHaveBeenCalled();
  });

  it("refuses wrong passwords and unknown accounts the same way", async () => {
    const auth = createIdentityAuth(CONFIG);
    for (const code of ["auth/wrong-password", "auth/user-not-found", "auth/user-disabled"]) {
      sdk.signInWithEmailAndPassword.mockRejectedValueOnce({ code });
      expect(await auth.signInWithPassword("x@example.test", "pw")).toEqual({ kind: "refused" });
    }
  });

  it("asks for TOTP when MFA is enrolled, then completes sign-in", async () => {
    const resolveSignIn = vi.fn(async () => {
      sdk.auth.currentUser = user();
    });
    sdk.signInWithEmailAndPassword.mockRejectedValueOnce({
      code: "auth/multi-factor-auth-required",
    });
    sdk.getMultiFactorResolver.mockReturnValue({
      hints: [{ factorId: "totp", uid: "factor-1" }],
      resolveSignIn,
    });
    const auth = createIdentityAuth(CONFIG);
    expect(await auth.signInWithPassword("owner@example.test", "pw")).toEqual({
      kind: "totp-required",
    });
    expect(await auth.verifyTotp("123456")).toEqual({ kind: "signed-in" });
    expect(sdk.assertionForSignIn).toHaveBeenCalledWith("factor-1", "123456");
    expect(resolveSignIn).toHaveBeenCalledWith("assertion");
  });

  it("refuses a bad TOTP code", async () => {
    sdk.signInWithEmailAndPassword.mockRejectedValueOnce({
      code: "auth/multi-factor-auth-required",
    });
    sdk.getMultiFactorResolver.mockReturnValue({
      hints: [{ factorId: "totp", uid: "factor-1" }],
      resolveSignIn: vi.fn(async () => {
        throw { code: "auth/invalid-verification-code" };
      }),
    });
    const auth = createIdentityAuth(CONFIG);
    await auth.signInWithPassword("owner@example.test", "pw");
    expect(await auth.verifyTotp("000000")).toEqual({ kind: "refused" });
  });

  it("sends a password reset and never reveals whether the account exists", async () => {
    const auth = createIdentityAuth(CONFIG);
    await expect(auth.sendPasswordReset("owner@example.test")).resolves.toBeUndefined();
    expect(sdk.sendPasswordResetEmail).toHaveBeenCalledWith(sdk.auth, "owner@example.test");
    sdk.sendPasswordResetEmail.mockRejectedValueOnce({ code: "auth/user-not-found" });
    await expect(auth.sendPasswordReset("nobody@example.test")).resolves.toBeUndefined();
  });

  it("reports only verified users to subscribers", async () => {
    let emit: (u: unknown) => void = () => {};
    sdk.onIdTokenChanged.mockImplementation((_auth: unknown, cb: (u: unknown) => void) => {
      emit = cb;
      return () => {};
    });
    const auth = createIdentityAuth(CONFIG);
    const listener = vi.fn();
    auth.subscribe(listener);
    await vi.waitFor(() => expect(sdk.onIdTokenChanged).toHaveBeenCalled());
    emit(user({ emailVerified: false }));
    expect(listener).toHaveBeenLastCalledWith(null);
    emit(user());
    expect(listener).toHaveBeenLastCalledWith({ email: "owner@example.test" });
  });

  it("has no token, and refuses sign-in, without an API key", async () => {
    const auth = createIdentityAuth({ ...CONFIG, firebaseApiKey: undefined });
    expect(await auth.getToken()).toBeNull();
    expect(await auth.signInWithPassword("owner@example.test", "pw")).toEqual({
      kind: "refused",
    });
  });

  describe("email actions", () => {
    it("checks a reset code and returns the account's email", async () => {
      const auth = createIdentityAuth(CONFIG);
      expect(await auth.checkPasswordResetCode("oob-1")).toEqual({
        kind: "valid",
        email: "owner@example.test",
      });
      expect(sdk.verifyPasswordResetCode).toHaveBeenCalledWith(sdk.auth, "oob-1");
    });

    it("reports an expired or used reset code as invalid", async () => {
      sdk.verifyPasswordResetCode.mockRejectedValueOnce({ code: "auth/expired-action-code" });
      const auth = createIdentityAuth(CONFIG);
      expect(await auth.checkPasswordResetCode("oob-1")).toEqual({ kind: "invalid" });
      sdk.verifyPasswordResetCode.mockRejectedValueOnce({ code: "auth/invalid-action-code" });
      expect(await auth.checkPasswordResetCode("oob-1")).toEqual({ kind: "invalid" });
    });

    it("sets the new password, and tells a weak password from a bad code", async () => {
      const auth = createIdentityAuth(CONFIG);
      expect(await auth.confirmPasswordReset("oob-1", "a-long-new-password")).toEqual({
        kind: "done",
      });
      expect(sdk.confirmPasswordReset).toHaveBeenCalledWith(
        sdk.auth,
        "oob-1",
        "a-long-new-password",
      );
      sdk.confirmPasswordReset.mockRejectedValueOnce({ code: "auth/weak-password" });
      expect(await auth.confirmPasswordReset("oob-1", "x")).toEqual({ kind: "weak-password" });
      sdk.confirmPasswordReset.mockRejectedValueOnce({
        code: "auth/password-does-not-meet-requirements",
      });
      expect(await auth.confirmPasswordReset("oob-1", "x")).toEqual({ kind: "weak-password" });
      sdk.confirmPasswordReset.mockRejectedValueOnce({ code: "auth/expired-action-code" });
      expect(await auth.confirmPasswordReset("oob-1", "a-long-new-password")).toEqual({
        kind: "invalid",
      });
    });

    it("verifies an email by applying the code", async () => {
      const auth = createIdentityAuth(CONFIG);
      expect(await auth.applyEmailAction("verifyEmail", "oob-2")).toEqual({ kind: "done" });
      expect(sdk.applyActionCode).toHaveBeenCalledWith(sdk.auth, "oob-2");
      expect(sdk.checkActionCode).not.toHaveBeenCalled();
      sdk.applyActionCode.mockRejectedValueOnce({ code: "auth/invalid-action-code" });
      expect(await auth.applyEmailAction("verifyEmail", "oob-2")).toEqual({ kind: "invalid" });
    });

    it("recovers an email by checking, then applying, the code", async () => {
      const auth = createIdentityAuth(CONFIG);
      expect(await auth.applyEmailAction("recoverEmail", "oob-3")).toEqual({ kind: "done" });
      expect(sdk.checkActionCode).toHaveBeenCalledWith(sdk.auth, "oob-3");
      expect(sdk.applyActionCode).toHaveBeenCalledWith(sdk.auth, "oob-3");
    });

    it("refuses a recover-email code for another operation, or one that fails the check", async () => {
      const auth = createIdentityAuth(CONFIG);
      sdk.checkActionCode.mockResolvedValueOnce({
        operation: "PASSWORD_RESET",
        data: { email: "owner@example.test" },
      });
      expect(await auth.applyEmailAction("recoverEmail", "oob-3")).toEqual({ kind: "invalid" });
      sdk.checkActionCode.mockRejectedValueOnce({ code: "auth/expired-action-code" });
      expect(await auth.applyEmailAction("recoverEmail", "oob-3")).toEqual({ kind: "invalid" });
      expect(sdk.applyActionCode).not.toHaveBeenCalled();
    });

    it("uses only the configured API key", async () => {
      const auth = createIdentityAuth(CONFIG);
      await auth.checkPasswordResetCode("oob-1");
      expect(sdk.initializeApp).toHaveBeenCalledWith({ apiKey: "test-key" });
      const keyless = createIdentityAuth({ ...CONFIG, firebaseApiKey: undefined });
      expect(await keyless.checkPasswordResetCode("oob-1")).toEqual({ kind: "invalid" });
      expect(await keyless.applyEmailAction("verifyEmail", "oob-2")).toEqual({ kind: "invalid" });
      expect(await keyless.confirmPasswordReset("oob-1", "a-long-new-password")).toEqual({
        kind: "invalid",
      });
    });
  });
});
