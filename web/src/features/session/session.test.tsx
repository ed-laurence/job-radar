import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthAdapter, SignInResult } from "@/lib/auth";
import { createFakeAuth } from "@/lib/auth";
import { API, renderWithApi } from "@/lib/testing";
import { AppFrame } from "./AppFrame";
import { RequireRole } from "./RequireRole";
import { RESET_SENT_MESSAGE, SignInScreen } from "./SignInScreen";

const router = { replace: vi.fn(), push: vi.fn() };
let pathname = "/roles";
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => pathname,
}));

const ME = {
  owner: {
    id: "00000000-0000-4000-8000-000000000001",
    email: "owner@example.test",
    role: "maintainer",
  },
  tester: {
    id: "00000000-0000-4000-8000-000000000002",
    email: "tester@example.test",
    role: "user",
  },
};

const server = setupServer(
  http.get(`${API}/me`, ({ request }) => {
    const auth = request.headers.get("authorization");
    if (auth === "Bearer fake:owner@example.test") return HttpResponse.json(ME.owner);
    if (auth === "Bearer fake:tester@example.test") return HttpResponse.json(ME.tester);
    return HttpResponse.json(
      {
        type: "/problems/unauthenticated",
        title: "Unauthenticated",
        status: 401,
        correlation_id: "c",
      },
      { status: 401 },
    );
  }),
  http.post(`${API}/events`, () => HttpResponse.json({ accepted: 1 }, { status: 202 })),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  router.replace.mockReset();
  router.push.mockReset();
  pathname = "/roles";
  window.history.replaceState(null, "", "/sign-in");
});

function identityAdapter(results: SignInResult[]): AuthAdapter & {
  signInWithPassword: ReturnType<typeof vi.fn>;
  verifyTotp: ReturnType<typeof vi.fn>;
  sendPasswordReset: ReturnType<typeof vi.fn>;
} {
  const queue = [...results];
  const next = async () => queue.shift() ?? { kind: "refused" as const };
  return {
    mode: "identity",
    subscribe: (l) => {
      l(null);
      return () => {};
    },
    getToken: async () => null,
    signInWithPassword: vi.fn(next),
    verifyTotp: vi.fn(next),
    sendPasswordReset: vi.fn(async () => {}),
    checkPasswordResetCode: async () => ({ kind: "invalid" as const }),
    confirmPasswordReset: async () => ({ kind: "invalid" as const }),
    applyEmailAction: async () => ({ kind: "invalid" as const }),
    signOut: async () => {},
  };
}

describe("SignInScreen", () => {
  it("has no sign-up or create-account link", async () => {
    renderWithApi(<SignInScreen />, { adapter: createFakeAuth(window.sessionStorage) });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /sign up|create|register/i })).toBeNull();
    expect(screen.queryByText(/sign up|create an account|register/i)).toBeNull();
  });

  it("fake mode: picks a placeholder user and goes to the requested page", async () => {
    window.history.replaceState(null, "", "/sign-in?next=%2Fcontacts");
    const auth = createFakeAuth(window.sessionStorage);
    renderWithApi(<SignInScreen />, { adapter: auth });
    await userEvent.click(await screen.findByRole("button", { name: /Owner \(maintainer\)/ }));
    expect(await auth.getToken()).toBe("fake:owner@example.test");
    expect(router.replace).toHaveBeenCalledWith("/contacts");
  });

  it("identity mode: a refused sign-in shows only the generic message", async () => {
    const auth = identityAdapter([{ kind: "refused" }]);
    renderWithApi(<SignInScreen />, { adapter: auth });
    await userEvent.type(await screen.findByLabelText("Email"), "tester@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This account can't sign in");
    expect(screen.getByRole("alert").textContent).toBe("This account can't sign in");
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("identity mode: asks for a TOTP code when MFA is required", async () => {
    const auth = identityAdapter([{ kind: "totp-required" }, { kind: "signed-in" }]);
    renderWithApi(<SignInScreen />, { adapter: auth });
    await userEvent.type(await screen.findByLabelText("Email"), "owner@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const code = await screen.findByLabelText("Authenticator code");
    expect(code).toHaveAttribute("autocomplete", "one-time-code");
    await userEvent.type(code, "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(auth.verifyTotp).toHaveBeenCalledWith("123456");
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("identity mode: forgot password sends a reset and shows the same text either way", async () => {
    const auth = identityAdapter([]);
    renderWithApi(<SignInScreen />, { adapter: auth });
    await userEvent.click(await screen.findByRole("button", { name: "Forgot password?" }));
    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Email"), "nobody@example.test");
    await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(auth.sendPasswordReset).toHaveBeenCalledWith("nobody@example.test");
    expect(await screen.findByRole("status")).toHaveTextContent(RESET_SENT_MESSAGE);
    expect(screen.queryByRole("alert")).toBeNull();

    // A failing request looks exactly the same.
    auth.sendPasswordReset.mockRejectedValueOnce(new Error("auth/too-many-requests"));
    await userEvent.type(screen.getByLabelText("Email"), "x");
    expect(screen.getByRole("status")).toHaveTextContent("");
    await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent(RESET_SENT_MESSAGE);

    await userEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("identity mode: ?reset=1 opens the reset form (from the email-action page)", async () => {
    window.history.replaceState(null, "", "/sign-in?reset=1");
    const auth = identityAdapter([]);
    renderWithApi(<SignInScreen />, { adapter: auth });
    expect(await screen.findByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.test");
    await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent(RESET_SENT_MESSAGE);
  });

  it("fake mode offers no password reset", async () => {
    renderWithApi(<SignInScreen />, { adapter: createFakeAuth(window.sessionStorage) });
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.queryByRole("button", { name: "Forgot password?" })).toBeNull();
  });

  it("shows the generic message after the API refused the account", async () => {
    window.history.replaceState(null, "", "/sign-in?refused=1");
    renderWithApi(<SignInScreen />, { adapter: createFakeAuth(window.sessionStorage) });
    expect(await screen.findByRole("alert")).toHaveTextContent("This account can't sign in");
  });
});

describe("AppFrame (route guard + shell)", () => {
  it("redirects a signed-out visitor to sign-in with the return path", async () => {
    renderWithApi(
      <AppFrame>
        <p>secret page</p>
      </AppFrame>,
      { adapter: createFakeAuth(window.sessionStorage) },
    );
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/sign-in?next=%2Froles"));
    expect(screen.queryByText("secret page")).toBeNull();
  });

  it("renders the top-bar shell for a signed-in maintainer, with no Analytics link", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    await auth.signInAs("owner@example.test");
    renderWithApi(
      <AppFrame actions={(me) => <button type="button">Health for {me.role}</button>}>
        <p>roles page</p>
      </AppFrame>,
      { adapter: auth },
    );
    expect(await screen.findByText("roles page")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["Dashboard", "Roles", "Contacts", "Employers", "Dev board", "Settings"]);
    expect(within(nav).getByRole("link", { name: "Employers" })).toHaveAttribute(
      "href",
      "/employers",
    );
    expect(screen.getByRole("button", { name: "Health for maintainer" })).toBeInTheDocument();
    expect(screen.getByText("owner@example.test")).toBeInTheDocument();
    expect(screen.getByText("· Maintainer")).toBeInTheDocument();
  });

  it("shows no Analytics link to a user-role account", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    await auth.signInAs("tester@example.test");
    renderWithApi(
      <AppFrame>
        <p>roles page</p>
      </AppFrame>,
      { adapter: auth },
    );
    expect(await screen.findByText("roles page")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Analytics" })).toBeNull();
  });

  it("signs out and returns to sign-in when the API rejects the account", async () => {
    server.use(
      http.get(`${API}/me`, () =>
        HttpResponse.json(
          { type: "/problems/forbidden", title: "Forbidden", status: 403, correlation_id: "c" },
          { status: 403 },
        ),
      ),
    );
    const auth = createFakeAuth(window.sessionStorage);
    await auth.signInAs("owner@example.test");
    renderWithApi(
      <AppFrame>
        <p>roles page</p>
      </AppFrame>,
      { adapter: auth },
    );
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/sign-in?refused=1"));
    expect(await auth.getToken()).toBeNull();
    expect(screen.queryByText("roles page")).toBeNull();
  });

  it("sign-out clears the session and goes to sign-in", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    await auth.signInAs("owner@example.test");
    renderWithApi(
      <AppFrame>
        <p>roles page</p>
      </AppFrame>,
      { adapter: auth },
    );
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await auth.getToken()).toBeNull();
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/sign-in"));
  });
});

describe("RequireRole", () => {
  it("blocks non-maintainers from maintainer pages", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    await auth.signInAs("tester@example.test");
    renderWithApi(
      <RequireRole requires="maintainer">
        <p>analytics</p>
      </RequireRole>,
      { adapter: auth },
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You don't have access to this page",
    );
    expect(screen.queryByText("analytics")).toBeNull();
  });

  it("lets maintainers through", async () => {
    const auth = createFakeAuth(window.sessionStorage);
    await auth.signInAs("owner@example.test");
    renderWithApi(
      <RequireRole requires="maintainer">
        <p>analytics</p>
      </RequireRole>,
      { adapter: auth },
    );
    expect(await screen.findByText("analytics")).toBeInTheDocument();
  });
});
