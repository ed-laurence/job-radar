"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { FAKE_USERS, isFakeAuth, type SignInResult, safeRedirectPath, useAuth } from "@/lib/auth";
import styles from "./SignInScreen.module.css";

/** One message for every failure, so the page never reveals which accounts exist. */
export const REFUSED_MESSAGE = "This account can't sign in";

/** Shown after a reset request whether or not the account exists. */
export const RESET_SENT_MESSAGE =
  "If that account can sign in, we've sent it a link to reset the password. Check your email.";

type Step = "credentials" | "totp" | "reset";

function nextFromLocation(): string {
  return safeRedirectPath(new URLSearchParams(window.location.search).get("next"));
}

export function SignInScreen() {
  const { adapter } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState<Step>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState(false);
  const [resetSent, setResetSent] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get("refused") === "1") setRefused(true);
    // "Forgot password?" on the email-action page links here.
    else if (query.get("reset") === "1") setStep("reset");
  }, []);

  const handle = (result: SignInResult) => {
    if (result.kind === "signed-in") {
      router.replace(nextFromLocation());
    } else if (result.kind === "totp-required") {
      setStep("totp");
      setRefused(false);
    } else {
      setRefused(true);
      setStep("credentials");
      setCode("");
    }
  };

  const run = async (action: () => Promise<SignInResult>) => {
    setBusy(true);
    setRefused(false);
    try {
      handle(await action());
    } catch {
      handle({ kind: "refused" });
    } finally {
      setBusy(false);
    }
  };

  const onCredentials = (e: FormEvent) => {
    e.preventDefault();
    void run(() => adapter.signInWithPassword(email.trim(), password));
  };

  const onTotp = (e: FormEvent) => {
    e.preventDefault();
    void run(() => adapter.verifyTotp(code.trim()));
  };

  const onReset = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await adapter.sendPasswordReset(email.trim());
    } catch {
      // The same message either way: never reveal whether the account exists.
    } finally {
      setBusy(false);
      setResetSent(true);
    }
  };

  const goTo = (next: Step) => {
    setStep(next);
    setRefused(false);
    setResetSent(false);
    setCode("");
  };

  return (
    <main className={styles.page}>
      <section className={styles.card} aria-labelledby="sign-in-title">
        <h1 id="sign-in-title">{step === "reset" ? "Reset your password" : "Sign in"}</h1>
        {refused ? (
          <p role="alert" className={styles.error}>
            {REFUSED_MESSAGE}
          </p>
        ) : null}

        {isFakeAuth(adapter) ? (
          <div className={styles.fake}>
            <p className={styles.hint}>Local mode: choose a placeholder account.</p>
            {FAKE_USERS.map((u) => (
              <button
                key={u.email}
                type="button"
                className={styles.primary}
                disabled={busy}
                onClick={() => void run(() => adapter.signInAs(u.email))}
              >
                {u.label}
                <span className={styles.secondaryText}> {u.email}</span>
              </button>
            ))}
          </div>
        ) : step === "credentials" ? (
          <form className={styles.form} onSubmit={onCredentials} noValidate>
            <label className={styles.label} htmlFor="sign-in-email">
              Email
            </label>
            <input
              id="sign-in-email"
              className={styles.input}
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <label className={styles.label} htmlFor="sign-in-password">
              Password
            </label>
            <input
              id="sign-in-password"
              className={styles.input}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="submit" className={styles.primary} disabled={busy}>
              Sign in
            </button>
            <button type="button" className={styles.link} onClick={() => goTo("reset")}>
              Forgot password?
            </button>
          </form>
        ) : step === "reset" ? (
          <form className={styles.form} onSubmit={(e) => void onReset(e)} noValidate>
            <p className={styles.hint}>Enter your email and we'll send a reset link.</p>
            <label className={styles.label} htmlFor="reset-email">
              Email
            </label>
            <input
              id="reset-email"
              className={styles.input}
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setResetSent(false);
              }}
            />
            <p role="status" className={styles.hint}>
              {resetSent ? RESET_SENT_MESSAGE : ""}
            </p>
            <button type="submit" className={styles.primary} disabled={busy || !email.trim()}>
              Send reset link
            </button>
            <button type="button" className={styles.link} onClick={() => goTo("credentials")}>
              Back to sign in
            </button>
          </form>
        ) : (
          <form className={styles.form} onSubmit={onTotp} noValidate>
            <label className={styles.label} htmlFor="sign-in-totp">
              Authenticator code
            </label>
            <input
              id="sign-in-totp"
              className={styles.input}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <button type="submit" className={styles.primary} disabled={busy}>
              Verify
            </button>
            <button type="button" className={styles.link} onClick={() => goTo("credentials")}>
              Use a different account
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
