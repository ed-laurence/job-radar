/**
 * Signs one account in through the app's real sign-in page and saves the session for the specs.
 *
 * Runs as its own Node process (spawned by auth.ts), outside the Playwright test runner, so no
 * trace, video or screenshot ever records the typed password or Identity Platform's sign-in
 * request. The credentials arrive in the environment (E2E_SIGNIN_EMAIL, E2E_SIGNIN_PASSWORD),
 * never on the command line, and are never printed.
 *
 *   node sign-in.mjs <base-url> <out-file>
 *
 * The out file (mode 0600, under web/.artifacts/) holds Playwright storage state plus the
 * Firebase session from sessionStorage: the app persists the session per tab, which storage
 * state alone doesn't capture.
 */
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { chromium } from "@playwright/test";

const [baseUrl, outFile] = process.argv.slice(2);
const email = process.env.E2E_SIGNIN_EMAIL;
const password = process.env.E2E_SIGNIN_PASSWORD;

function fail(reason) {
  process.stderr.write(`${reason}\n`);
  process.exit(2);
}

if (!baseUrl || !outFile) fail("usage: sign-in.mjs <base-url> <out-file>");
if (!email || !password) fail("sign-in: credentials not provided");

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ baseURL: baseUrl });
  const page = await context.newPage();
  // Identity Platform's answer to the password sign-in: its error code (e.g.
  // INVALID_LOGIN_CREDENTIALS) explains a refusal and carries no credential.
  let idpAnswer = "no sign-in request seen";
  page.on("response", async (response) => {
    if (!response.url().includes("accounts:signInWithPassword")) return;
    const sent = response.request().postDataJSON() ?? {};
    const same =
      sent.email === email && sent.password === password ? "" : "; the form sent other values";
    if (response.ok()) {
      idpAnswer = "Identity Platform accepted the password";
      return;
    }
    const body = await response.json().catch(() => ({}));
    const code = String(body?.error?.message ?? response.status()).replace(/[^A-Z0-9_ :-]/gi, "");
    idpAnswer = `Identity Platform answered ${code.slice(0, 80)}${same}`;
  });
  await page.goto("/sign-in?next=%2Fdashboard");
  // The inputs are controlled: typing before React hydrates them is lost and an empty form is
  // submitted (refused). Wait until React has attached its props to the email field.
  await page.waitForFunction(
    () => {
      const input = document.querySelector("#sign-in-email");
      return Boolean(input && Object.keys(input).some((k) => k.startsWith("__reactProps")));
    },
    undefined,
    { timeout: 30_000 },
  );
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  const outcome = await Promise.race([
    page.waitForURL(/\/dashboard$/, { timeout: 30_000 }).then(() => "signed-in"),
    // The app's one refusal message (Next's route announcer is also role="alert").
    page
      .getByText("This account can't sign in")
      .waitFor({ timeout: 30_000 })
      .then(() => "refused"),
    page
      .locator("#sign-in-totp")
      .waitFor({ timeout: 30_000 })
      .then(() => "totp"),
  ]).catch(() => "timeout");
  if (outcome === "refused") {
    fail(
      `sign-in: the app refused the account (${idpAnswer}; accepted means the email isn't verified)`,
    );
  }
  if (outcome === "totp")
    fail("sign-in: the account needs a TOTP code; use an e2e account without MFA");
  if (outcome !== "signed-in") fail("sign-in: no redirect to the dashboard within 30 s");

  // The app is signed in once /me answered; wait for the shell, then copy the Firebase session.
  await page.getByRole("button", { name: "Sign out" }).waitFor({ timeout: 30_000 });
  const session = await page.evaluate(() =>
    Object.keys(sessionStorage)
      .filter((k) => k.startsWith("firebase:"))
      .map((k) => [k, sessionStorage.getItem(k) ?? ""]),
  );
  if (!session.some(([k]) => k.startsWith("firebase:authUser:"))) {
    fail("sign-in: signed in, but no Firebase session in sessionStorage");
  }
  const state = await context.storageState();
  const origin = new URL(baseUrl).origin;
  mkdirSync(dirname(outFile), { recursive: true });
  const tmp = `${outFile}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify({ origin, state, session }), { mode: 0o600 });
  renameSync(tmp, outFile);
  process.stdout.write("signed in\n");
} finally {
  await browser.close();
}
