"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { type AuthAdapter, safeRedirectPath, useAuth } from "@/lib/auth";
import styles from "./EmailActionScreen.module.css";
import base from "./SignInScreen.module.css";

/** One message for every bad link (expired, used, unknown mode), so nothing about it leaks. */
export const ACTION_ERROR_MESSAGE =
  "This link has expired or has already been used. Request a new one, or sign in.";

export const MIN_PASSWORD_LENGTH = 12;

const FORGOT_HREF = "/sign-in?reset=1";

/** `e•••@domain`: enough to recognise the account, not enough to read it off a screen. */
export function maskEmail(email: string): string {
  const at = email.lastIndexOf("@");
  if (at < 0) return "•••";
  return `${email.slice(0, Math.min(at, 1))}•••@${email.slice(at + 1)}`;
}

/**
 * Where to go after sign-in, from the link's `continueUrl`. Identity Platform sends an absolute
 * URL, so a same-origin one is reduced to its path first; everything goes through
 * `safeRedirectPath`, and anything else becomes its default.
 */
export function continuePath(raw: string | null, origin: string): string | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return safeRedirectPath(null);
  }
  if (url.origin !== origin) return safeRedirectPath(null);
  return safeRedirectPath(`${url.pathname}${url.search}${url.hash}`);
}

type View =
  | { kind: "loading" }
  | { kind: "reset"; email: string }
  | { kind: "reset-done" }
  | { kind: "verified" }
  | { kind: "recovered" }
  | { kind: "error" };

interface Initial {
  view: View;
  /** Kept in memory for the reset form only; never rendered or logged. */
  code: string | null;
  next: string | null;
}

/**
 * Reads the link once, strips it from the address bar (so the one-time code isn't left in
 * history), and runs the first step. The `apiKey` parameter is ignored on purpose: the adapter
 * only uses the configured key.
 */
async function start(adapter: AuthAdapter): Promise<Initial> {
  const query = new URLSearchParams(window.location.search);
  const mode = query.get("mode");
  const code = query.get("oobCode");
  const next = continuePath(query.get("continueUrl"), window.location.origin);
  window.history.replaceState(null, "", window.location.pathname);

  const fail: Initial = { view: { kind: "error" }, code: null, next };
  if (!code) return fail;
  if (mode === "resetPassword") {
    const check = await adapter.checkPasswordResetCode(code);
    return check.kind === "valid"
      ? { view: { kind: "reset", email: check.email }, code, next }
      : fail;
  }
  if (mode === "verifyEmail" || mode === "recoverEmail") {
    const result = await adapter.applyEmailAction(mode, code);
    if (result.kind !== "done") return fail;
    return { view: { kind: mode === "verifyEmail" ? "verified" : "recovered" }, code: null, next };
  }
  return fail;
}

type FieldError = { field: "password" | "confirm" | "form"; message: string } | null;

export function EmailActionScreen() {
  const { adapter } = useAuth();
  const started = useRef<Promise<Initial> | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [view, setView] = useState<View>({ kind: "loading" });
  const [code, setCode] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // One run per page, even when React re-runs effects: a code works only once.
    started.current ??= start(adapter).catch(
      (): Initial => ({ view: { kind: "error" }, code: null, next: null }),
    );
    void started.current.then((initial) => {
      if (!live) return;
      setCode(initial.code);
      setNext(initial.next);
      setView(initial.view);
    });
    return () => {
      live = false;
    };
  }, [adapter]);

  // Move focus to the new heading so screen readers announce each step.
  useEffect(() => {
    if (view.kind !== "loading") heading.current?.focus();
  }, [view.kind]);

  const signInHref = next ? `/sign-in?next=${encodeURIComponent(next)}` : "/sign-in";

  const title =
    view.kind === "loading"
      ? "Checking your link"
      : view.kind === "reset"
        ? `Set a new password for ${maskEmail(view.email)}`
        : view.kind === "reset-done"
          ? "Password updated"
          : view.kind === "verified"
            ? "Email verified"
            : view.kind === "recovered"
              ? "Email restored"
              : "This link can't be used";

  return (
    <main className={base.page}>
      <section className={base.card} aria-labelledby="email-action-title">
        <h1 id="email-action-title" ref={heading} tabIndex={-1} className={styles.title}>
          {title}
        </h1>

        {view.kind === "loading" ? (
          <p role="status" className={base.hint}>
            One moment…
          </p>
        ) : view.kind === "reset" && code ? (
          <ResetForm
            code={code}
            adapter={adapter}
            onDone={() => setView({ kind: "reset-done" })}
            onInvalid={() => {
              setCode(null);
              setView({ kind: "error" });
            }}
          />
        ) : view.kind === "error" ? (
          <div className={base.form}>
            <p role="alert" className={base.error}>
              {ACTION_ERROR_MESSAGE}
            </p>
            <a className={`${base.primary} ${styles.action}`} href={signInHref}>
              Sign in
            </a>
            <a className={base.link} href={FORGOT_HREF}>
              Forgot password?
            </a>
          </div>
        ) : (
          <div className={base.form}>
            <p role="status" className={base.hint}>
              {view.kind === "reset-done"
                ? "Your password has been changed. Sign in with the new one."
                : view.kind === "verified"
                  ? "Thanks, your email address is confirmed. You can sign in now."
                  : "Your sign-in email has been changed back. If you didn't ask for the change, reset your password too."}
            </p>
            <a className={`${base.primary} ${styles.action}`} href={signInHref}>
              Sign in
            </a>
            {view.kind === "recovered" ? (
              <a className={base.link} href={FORGOT_HREF}>
                Forgot password?
              </a>
            ) : null}
          </div>
        )}
      </section>
    </main>
  );
}

interface ResetFormProps {
  code: string;
  adapter: AuthAdapter;
  onDone: () => void;
  onInvalid: () => void;
}

function ResetForm({ code, adapter, onDone, onInvalid }: ResetFormProps) {
  const id = useId();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<FieldError>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError({ field: "password", message: `Use at least ${MIN_PASSWORD_LENGTH} characters.` });
      return;
    }
    if (password !== confirm) {
      setError({ field: "confirm", message: "The passwords don't match." });
      return;
    }
    setError(null);
    setBusy(true);
    let result: Awaited<ReturnType<AuthAdapter["confirmPasswordReset"]>>;
    try {
      result = await adapter.confirmPasswordReset(code, password);
    } catch {
      result = { kind: "invalid" };
    }
    setBusy(false);
    if (result.kind === "done") onDone();
    else if (result.kind === "weak-password") {
      setError({
        field: "password",
        message: "That password doesn't meet the password rules. Try a longer one.",
      });
    } else onInvalid();
  };

  return (
    <form className={base.form} onSubmit={(e) => void onSubmit(e)} noValidate>
      <PasswordField
        id={`${id}-password`}
        label="New password"
        value={password}
        onChange={setPassword}
        hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
        error={error?.field === "password" ? error.message : null}
      />
      <PasswordField
        id={`${id}-confirm`}
        label="Confirm new password"
        value={confirm}
        onChange={setConfirm}
        error={error?.field === "confirm" ? error.message : null}
      />
      <button type="submit" className={base.primary} disabled={busy}>
        Set password
      </button>
    </form>
  );
}

interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error: string | null;
}

function PasswordField({ id, label, value, onChange, hint, error }: PasswordFieldProps) {
  const [shown, setShown] = useState(false);
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={styles.field}>
      <label className={base.label} htmlFor={id}>
        {label}
      </label>
      <div className={styles.row}>
        <input
          id={id}
          className={`${base.input} ${styles.input}`}
          type={shown ? "text" : "password"}
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className={styles.toggle}
          aria-controls={id}
          aria-label={`${shown ? "Hide" : "Show"} ${label.toLowerCase()}`}
          onClick={() => setShown((s) => !s)}
        >
          {shown ? "Hide" : "Show"}
        </button>
      </div>
      {hint ? (
        <p id={hintId} className={base.hint}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className={styles.fieldError}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
