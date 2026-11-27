import type {
  Auth,
  MultiFactorResolver,
  TotpMultiFactorGenerator as TotpGen,
} from "@firebase/auth";
import type { AuthConfig } from "./config";
import type { AuthAdapter, EmailActionResult, SignedInUser, SignInResult } from "./types";

const WEAK_PASSWORD_CODES = new Set([
  "auth/weak-password",
  "auth/password-does-not-meet-requirements",
]);

interface Loaded {
  auth: Auth;
  mod: typeof import("@firebase/auth");
}

/**
 * Identity Platform (Firebase Auth) with email/password and TOTP. Only the public web API key is
 * configured: email/password sign-in needs no authDomain, so no project id reaches the bundle.
 * The SDK is loaded lazily, and only in this mode. The session persists for the browser tab
 * (reloads keep you signed in) and the SDK refreshes the hourly ID token itself.
 */
export function createIdentityAuth(config: AuthConfig): AuthAdapter {
  let loading: Promise<Loaded> | undefined;
  let resolver: MultiFactorResolver | undefined;

  const load = (): Promise<Loaded> => {
    loading ??= (async () => {
      if (!config.firebaseApiKey) throw new Error("NEXT_PUBLIC_FIREBASE_API_KEY is not set");
      const [{ initializeApp }, mod] = await Promise.all([
        import("@firebase/app"),
        import("@firebase/auth"),
      ]);
      const app = initializeApp({ apiKey: config.firebaseApiKey });
      const auth = mod.initializeAuth(app, {
        persistence: mod.browserSessionPersistence,
      });
      if (config.tenantId) auth.tenantId = config.tenantId;
      return { auth, mod };
    })();
    return loading;
  };

  const finish = async ({ auth, mod }: Loaded): Promise<SignInResult> => {
    // The API checks email_verified too; refusing here avoids a confusing half sign-in.
    if (!auth.currentUser?.emailVerified) {
      await mod.signOut(auth);
      return { kind: "refused" };
    }
    return { kind: "signed-in" };
  };

  return {
    mode: "identity",
    subscribe(listener) {
      let unsubscribe: (() => void) | undefined;
      let cancelled = false;
      load()
        .then(({ auth, mod }) => {
          if (cancelled) return;
          unsubscribe = mod.onIdTokenChanged(auth, (user) => {
            const next: SignedInUser | null =
              user?.email && user.emailVerified ? { email: user.email } : null;
            listener(next);
          });
        })
        .catch(() => listener(null));
      return () => {
        cancelled = true;
        unsubscribe?.();
      };
    },
    async getToken() {
      try {
        const { auth } = await load();
        // After a reload the persisted session is restored asynchronously; wait for it so the
        // first requests carry a token. getIdToken() refreshes an expired (or soon to expire)
        // ID token with the refresh token, so every request sends a fresh one.
        await auth.authStateReady();
        return (await auth.currentUser?.getIdToken()) ?? null;
      } catch {
        return null;
      }
    },
    async signInWithPassword(email, password) {
      try {
        const loaded = await load();
        resolver = undefined;
        try {
          await loaded.mod.signInWithEmailAndPassword(loaded.auth, email, password);
        } catch (error) {
          const code = (error as { code?: unknown }).code;
          if (code !== "auth/multi-factor-auth-required") return { kind: "refused" };
          resolver = loaded.mod.getMultiFactorResolver(
            loaded.auth,
            error as Parameters<typeof loaded.mod.getMultiFactorResolver>[1],
          );
          const hasTotp = resolver.hints.some(
            (h) => h.factorId === loaded.mod.TotpMultiFactorGenerator.FACTOR_ID,
          );
          return hasTotp ? { kind: "totp-required" } : { kind: "refused" };
        }
        return await finish(loaded);
      } catch {
        return { kind: "refused" };
      }
    },
    async verifyTotp(code) {
      try {
        const loaded = await load();
        const gen: typeof TotpGen = loaded.mod.TotpMultiFactorGenerator;
        const hint = resolver?.hints.find((h) => h.factorId === gen.FACTOR_ID);
        if (!resolver || !hint) return { kind: "refused" };
        await resolver.resolveSignIn(gen.assertionForSignIn(hint.uid, code));
        resolver = undefined;
        return await finish(loaded);
      } catch {
        return { kind: "refused" };
      }
    },
    async sendPasswordReset(email) {
      try {
        const { auth, mod } = await load();
        await mod.sendPasswordResetEmail(auth, email);
      } catch {
        // Unknown accounts, throttling and network errors all look the same to the user.
      }
    },
    // Email-action codes: every failure (expired, used, malformed, network) is "invalid", and
    // the code itself is never logged or returned.
    async checkPasswordResetCode(code) {
      try {
        const { auth, mod } = await load();
        const email = await mod.verifyPasswordResetCode(auth, code);
        return { kind: "valid", email };
      } catch {
        return { kind: "invalid" };
      }
    },
    async confirmPasswordReset(code, newPassword): Promise<EmailActionResult> {
      try {
        const { auth, mod } = await load();
        await mod.confirmPasswordReset(auth, code, newPassword);
        return { kind: "done" };
      } catch (error) {
        const errorCode = (error as { code?: unknown } | null)?.code;
        return typeof errorCode === "string" && WEAK_PASSWORD_CODES.has(errorCode)
          ? { kind: "weak-password" }
          : { kind: "invalid" };
      }
    },
    async applyEmailAction(mode, code): Promise<EmailActionResult> {
      try {
        const { auth, mod } = await load();
        if (mode === "recoverEmail") {
          const info = await mod.checkActionCode(auth, code);
          if (info.operation !== "RECOVER_EMAIL") return { kind: "invalid" };
        }
        await mod.applyActionCode(auth, code);
        return { kind: "done" };
      } catch {
        return { kind: "invalid" };
      }
    },
    async signOut() {
      resolver = undefined;
      const { auth, mod } = await load();
      await mod.signOut(auth);
    },
  };
}
