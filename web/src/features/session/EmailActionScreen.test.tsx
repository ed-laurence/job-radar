import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthAdapter, EmailActionResult, ResetCodeCheck } from "@/lib/auth";
import { createFakeAuth } from "@/lib/auth";
import { renderWithApi } from "@/lib/testing";
import {
  ACTION_ERROR_MESSAGE,
  continuePath,
  EmailActionScreen,
  maskEmail,
} from "./EmailActionScreen";

const OOB = "secret-oob-code-123";
const KEY = "attacker-api-key-xyz";

function adapter(
  over: { check?: ResetCodeCheck; confirm?: EmailActionResult[]; apply?: EmailActionResult } = {},
) {
  const confirms = [...(over.confirm ?? [])];
  return {
    mode: "identity" as const,
    subscribe: (l: (u: null) => void) => {
      l(null);
      return () => {};
    },
    getToken: async () => null,
    signInWithPassword: vi.fn(async () => ({ kind: "refused" as const })),
    verifyTotp: vi.fn(async () => ({ kind: "refused" as const })),
    sendPasswordReset: vi.fn(async () => {}),
    checkPasswordResetCode: vi.fn(
      async (_code: string): Promise<ResetCodeCheck> =>
        over.check ?? { kind: "valid", email: "owner@example.test" },
    ),
    confirmPasswordReset: vi.fn(
      async (_code: string, _pw: string): Promise<EmailActionResult> =>
        confirms.shift() ?? { kind: "done" },
    ),
    applyEmailAction: vi.fn(
      async (_mode: string, _code: string): Promise<EmailActionResult> =>
        over.apply ?? { kind: "done" },
    ),
    signOut: async () => {},
  } satisfies AuthAdapter;
}

function visit(query: string) {
  window.history.replaceState(null, "", `/auth/action${query}`);
}

function expectNoSecrets() {
  expect(document.body.innerHTML).not.toContain(OOB);
  expect(document.body.innerHTML).not.toContain(KEY);
}

const NEW_PASSWORD = "correct-horse-battery";

beforeEach(() => {
  visit("");
});

describe("maskEmail", () => {
  it("keeps the first character and the domain", () => {
    expect(maskEmail("owner@example.test")).toBe("o•••@example.test");
    expect(maskEmail("e@domain.test")).toBe("e•••@domain.test");
  });

  it("masks odd input completely", () => {
    expect(maskEmail("no-at-sign")).toBe("•••");
    expect(maskEmail("@domain.test")).toBe("•••@domain.test");
    expect(maskEmail("")).toBe("•••");
  });
});

describe("continuePath", () => {
  const origin = "http://127.0.0.1:3000";
  it("follows only same-origin paths, through safeRedirectPath", () => {
    expect(continuePath(null, origin)).toBeNull();
    expect(continuePath("http://127.0.0.1:3000/contacts?x=1", origin)).toBe("/contacts?x=1");
    expect(continuePath("/roles", origin)).toBe("/roles");
    expect(continuePath("https://evil.example/contacts", origin)).toBe("/dashboard");
    expect(continuePath("//evil.example/x", origin)).toBe("/dashboard");
    expect(continuePath("javascript:alert(1)", origin)).toBe("/dashboard");
    expect(continuePath("http://127.0.0.1:3000/sign-in", origin)).toBe("/dashboard");
  });
});

describe("EmailActionScreen", () => {
  it("resetPassword: shows the masked email, validates, and sets the new password", async () => {
    visit(`?mode=resetPassword&oobCode=${OOB}&apiKey=${KEY}&lang=en`);
    const auth = adapter();
    renderWithApi(<EmailActionScreen />, { adapter: auth });

    expect(
      await screen.findByRole("heading", { name: "Set a new password for o•••@example.test" }),
    ).toBeInTheDocument();
    expect(auth.checkPasswordResetCode).toHaveBeenCalledWith(OOB);
    expect(screen.queryByText("owner@example.test")).toBeNull();
    expectNoSecrets();
    // The code leaves the address bar once read.
    expect(window.location.search).toBe("");

    const pw = screen.getByLabelText("New password");
    const confirm = screen.getByLabelText("Confirm new password");
    expect(pw).toHaveAttribute("type", "password");
    expect(pw).toHaveAttribute("autocomplete", "new-password");

    // Too short.
    await userEvent.type(pw, "short");
    await userEvent.type(confirm, "short");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 12 characters");
    expect(pw).toHaveAttribute("aria-invalid", "true");
    expect(auth.confirmPasswordReset).not.toHaveBeenCalled();

    // Not matching.
    await userEvent.clear(pw);
    await userEvent.clear(confirm);
    await userEvent.type(pw, NEW_PASSWORD);
    await userEvent.type(confirm, `${NEW_PASSWORD}!`);
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("don't match");
    expect(confirm).toHaveAttribute("aria-invalid", "true");
    expect(auth.confirmPasswordReset).not.toHaveBeenCalled();

    await userEvent.clear(confirm);
    await userEvent.type(confirm, NEW_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));

    expect(await screen.findByRole("heading", { name: "Password updated" })).toBeInTheDocument();
    expect(auth.confirmPasswordReset).toHaveBeenCalledWith(OOB, NEW_PASSWORD);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
    expectNoSecrets();
  });

  it("resetPassword: 11 characters is too short, 12 is enough", async () => {
    visit(`?mode=resetPassword&oobCode=${OOB}`);
    const auth = adapter();
    renderWithApi(<EmailActionScreen />, { adapter: auth });
    const pw = await screen.findByLabelText("New password");
    const confirm = screen.getByLabelText("Confirm new password");
    expect(screen.getByText("At least 12 characters.")).toBeInTheDocument();
    await userEvent.type(pw, "12345678901");
    await userEvent.type(confirm, "12345678901");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use at least 12 characters.");
    expect(auth.confirmPasswordReset).not.toHaveBeenCalled();
    await userEvent.type(pw, "2");
    await userEvent.type(confirm, "2");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("heading", { name: "Password updated" })).toBeInTheDocument();
    expect(auth.confirmPasswordReset).toHaveBeenCalledWith(OOB, "123456789012");
  });

  it("resetPassword: show/hide toggles reveal each field", async () => {
    visit(`?mode=resetPassword&oobCode=${OOB}`);
    renderWithApi(<EmailActionScreen />, { adapter: adapter() });
    const pw = await screen.findByLabelText("New password");
    const confirm = screen.getByLabelText("Confirm new password");
    await userEvent.click(screen.getByRole("button", { name: "Show new password" }));
    expect(pw).toHaveAttribute("type", "text");
    expect(confirm).toHaveAttribute("type", "password");
    const hide = screen.getByRole("button", { name: "Hide new password" });
    expect(hide).toHaveAttribute("aria-controls", pw.id);
    await userEvent.click(screen.getByRole("button", { name: "Show confirm new password" }));
    expect(confirm).toHaveAttribute("type", "text");
    await userEvent.click(hide);
    expect(pw).toHaveAttribute("type", "password");
  });

  it("resetPassword: a password the policy rejects keeps the form with a hint", async () => {
    visit(`?mode=resetPassword&oobCode=${OOB}`);
    const auth = adapter({ confirm: [{ kind: "weak-password" }] });
    renderWithApi(<EmailActionScreen />, { adapter: auth });
    await userEvent.type(await screen.findByLabelText("New password"), NEW_PASSWORD);
    await userEvent.type(screen.getByLabelText("Confirm new password"), NEW_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/password rules/i);
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });

  it("resetPassword: a code that expires before submit shows the generic error", async () => {
    visit(`?mode=resetPassword&oobCode=${OOB}`);
    const auth = adapter({ confirm: [{ kind: "invalid" }] });
    renderWithApi(<EmailActionScreen />, { adapter: auth });
    await userEvent.type(await screen.findByLabelText("New password"), NEW_PASSWORD);
    await userEvent.type(screen.getByLabelText("Confirm new password"), NEW_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(ACTION_ERROR_MESSAGE);
    expect(screen.queryByLabelText("New password")).toBeNull();
  });

  it("resetPassword: an expired or used code shows the generic error with recovery links", async () => {
    visit(`?mode=resetPassword&oobCode=${OOB}&apiKey=${KEY}`);
    renderWithApi(<EmailActionScreen />, { adapter: adapter({ check: { kind: "invalid" } }) });
    expect(await screen.findByRole("alert")).toHaveTextContent(ACTION_ERROR_MESSAGE);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute(
      "href",
      "/sign-in?reset=1",
    );
    expect(screen.queryByLabelText("New password")).toBeNull();
    expectNoSecrets();
  });

  it("verifyEmail: applies the code and confirms", async () => {
    visit(`?mode=verifyEmail&oobCode=${OOB}&apiKey=${KEY}`);
    const auth = adapter();
    renderWithApi(<EmailActionScreen />, { adapter: auth });
    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeInTheDocument();
    expect(auth.applyEmailAction).toHaveBeenCalledWith("verifyEmail", OOB);
    expect(auth.applyEmailAction).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
    expectNoSecrets();
  });

  it("verifyEmail: a used code shows the generic error", async () => {
    visit(`?mode=verifyEmail&oobCode=${OOB}`);
    renderWithApi(<EmailActionScreen />, { adapter: adapter({ apply: { kind: "invalid" } }) });
    expect(await screen.findByRole("alert")).toHaveTextContent(ACTION_ERROR_MESSAGE);
  });

  it("recoverEmail: applies the code and suggests a password reset", async () => {
    visit(`?mode=recoverEmail&oobCode=${OOB}`);
    const auth = adapter();
    renderWithApi(<EmailActionScreen />, { adapter: auth });
    expect(await screen.findByRole("heading", { name: "Email restored" })).toBeInTheDocument();
    expect(auth.applyEmailAction).toHaveBeenCalledWith("recoverEmail", OOB);
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute(
      "href",
      "/sign-in?reset=1",
    );
  });

  it("follows continueUrl only through safeRedirectPath", async () => {
    visit(
      `?mode=verifyEmail&oobCode=${OOB}&continueUrl=${encodeURIComponent(
        `${window.location.origin}/contacts`,
      )}`,
    );
    renderWithApi(<EmailActionScreen />, { adapter: adapter() });
    expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in?next=%2Fcontacts",
    );
  });

  it("never follows a foreign continueUrl", async () => {
    visit(
      `?mode=verifyEmail&oobCode=${OOB}&continueUrl=${encodeURIComponent("https://evil.example/x")}`,
    );
    renderWithApi(<EmailActionScreen />, { adapter: adapter() });
    const link = await screen.findByRole("link", { name: "Sign in" });
    expect(link.getAttribute("href")).not.toContain("evil");
  });

  it.each([
    ["an unknown mode", `?mode=signIn&oobCode=${OOB}`],
    ["a missing mode", `?oobCode=${OOB}`],
    ["a missing code", "?mode=resetPassword"],
  ])("shows the generic error for %s without calling the adapter", async (_name, query) => {
    visit(query);
    const auth = adapter();
    renderWithApi(<EmailActionScreen />, { adapter: auth });
    expect(await screen.findByRole("alert")).toHaveTextContent(ACTION_ERROR_MESSAGE);
    expect(auth.checkPasswordResetCode).not.toHaveBeenCalled();
    expect(auth.applyEmailAction).not.toHaveBeenCalled();
    expectNoSecrets();
  });

  it("fake mode: every link is invalid", async () => {
    visit(`?mode=verifyEmail&oobCode=${OOB}`);
    renderWithApi(<EmailActionScreen />, { adapter: createFakeAuth(window.sessionStorage) });
    expect(await screen.findByRole("alert")).toHaveTextContent(ACTION_ERROR_MESSAGE);
  });

  it("never logs the code", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) =>
      vi.spyOn(console, m),
    );
    visit(`?mode=resetPassword&oobCode=${OOB}&apiKey=${KEY}`);
    renderWithApi(<EmailActionScreen />, { adapter: adapter({ check: { kind: "invalid" } }) });
    await screen.findByRole("alert");
    await waitFor(() => {
      for (const spy of spies) {
        for (const call of spy.mock.calls) {
          expect(JSON.stringify(call)).not.toContain(OOB);
          expect(JSON.stringify(call)).not.toContain(KEY);
        }
      }
    });
  });
});
