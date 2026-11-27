import type { AuthMode } from "./config";

export interface SignedInUser {
  email: string;
}

export type SignInResult =
  | { kind: "signed-in" }
  | { kind: "totp-required" }
  /** Any failure. The UI shows one generic message and never says why. */
  | { kind: "refused" };

/** The one-time email links Identity Platform sends: `/auth/action?mode=…&oobCode=…`. */
export type EmailActionMode = "resetPassword" | "verifyEmail" | "recoverEmail";

export type ResetCodeCheck =
  | { kind: "valid"; email: string }
  /** Expired, already used, malformed or for another account: the UI shows one generic error. */
  | { kind: "invalid" };

export type EmailActionResult =
  | { kind: "done" }
  /** The new password doesn't meet the project's password policy. */
  | { kind: "weak-password" }
  | { kind: "invalid" };

export interface AuthAdapter {
  readonly mode: AuthMode;
  /** Calls `listener` with the current user now and on every change. Returns an unsubscribe. */
  subscribe(listener: (user: SignedInUser | null) => void): () => void;
  getToken(): Promise<string | null>;
  signInWithPassword(email: string, password: string): Promise<SignInResult>;
  verifyTotp(code: string): Promise<SignInResult>;
  /**
   * Asks for a password-reset email. Always resolves, whether or not the account exists or the
   * request failed, so the UI can't reveal which accounts exist.
   */
  sendPasswordReset(email: string): Promise<void>;
  /** Checks a password-reset code and returns the account's email. Never throws. */
  checkPasswordResetCode(code: string): Promise<ResetCodeCheck>;
  /** Sets the new password for a reset code. Never throws. */
  confirmPasswordReset(code: string, newPassword: string): Promise<EmailActionResult>;
  /** Applies a verify-email or recover-email code. Never throws. */
  applyEmailAction(
    mode: Exclude<EmailActionMode, "resetPassword">,
    code: string,
  ): Promise<EmailActionResult>;
  signOut(): Promise<void>;
}
