import { expect, test } from "@playwright/test";
import { signIn } from "./support/auth";
import { api, Created } from "./support/cleanup";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const API_HEALTH_URL = process.env.E2E_API_HEALTH_URL ?? "http://127.0.0.1:8000/healthz";
const TRIAGE_TIMEOUT_MS = 90_000;
const REPLY_TIMEOUT_MS = 90_000;

/**
 * The recorded task-chat conversation (backend/tests/fixtures/llm/devboard/task_chat/, see
 * backend/tests/unit/devboard/chat_cards.py). Replay is keyed by the prompt, so the card's title,
 * body and column (Triaged, no plan) and the message must match the recording exactly.
 */
const CARD = {
  title: "Add a contacts search endpoint",
  body: "Let users search contacts by name. Return matches ordered by last contact date.",
};
const MESSAGE = "Can you make the acceptance criteria testable?";
const CRITERION = "Searching by part of a name returns only the matching contacts";

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

interface TaskJson {
  id: string;
  version: number;
  column: string;
}

async function createCard(created: Created): Promise<TaskJson> {
  const res = await api("/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(CARD),
  });
  expect(res.status).toBe(201);
  const task = (await res.json()) as TaskJson;
  created.add("tasks", task.id, task.version);
  return task;
}

/** Polls the card until triage moves it out of Backlog; returns where it landed. */
async function waitForTriage(id: string): Promise<string> {
  let column = "backlog";
  await expect(async () => {
    const res = await api(`/tasks/${id}`);
    column = ((await res.json()) as TaskJson).column;
    expect(column).not.toBe("backlog");
  }).toPass({ timeout: TRIAGE_TIMEOUT_MS });
  return column;
}

test.describe("dev board task chat", () => {
  let created = new Created();
  test.beforeEach(() => {
    created = new Created();
  });
  test.afterEach(async () => {
    await created.cleanup();
  });

  test.beforeAll(async () => {
    const [web, apiUp] = await Promise.all([
      reachable(`${BASE_URL}/sign-in`),
      reachable(API_HEALTH_URL),
    ]);
    // Needs scripts/dev.sh running web + api (AUTH=fake, LLM=replay); skipped, never passed, when either is down.
    test.skip(
      !web || !apiUp,
      `backend not runnable: web ${web ? "up" : "down"} at ${BASE_URL}, api ${apiUp ? "up" : "down"} at ${API_HEALTH_URL}. Start scripts/dev.sh.`,
    );
  });

  test("discusses a card, accepts the proposed edit and clears the chat", async ({ page }) => {
    await signIn(page, "owner", "/dev-board");
    const card = await createCard(created);
    const column = await waitForTriage(card.id);
    test.skip(column !== "triaged", `the recording needs a Triaged card; triage chose ${column}`);

    await page.goto(`/dev-board/${card.id}`);
    await expect(page.getByRole("heading", { level: 1, name: CARD.title })).toBeVisible();
    const chat = page.getByRole("region", { name: "Task chat" });
    await expect(chat.getByRole("heading", { name: "Discuss this task" })).toBeVisible();

    // Enter sends; the reply streams into the conversation window.
    const input = chat.getByRole("textbox", { name: "Message" });
    await input.fill(MESSAGE);
    await input.press("Enter");
    await expect(input).toBeFocused();
    const log = chat.getByRole("log", { name: "Conversation" });
    await expect(log.getByText(MESSAGE)).toBeVisible();
    await expect(chat.getByRole("status")).toHaveText("Claude replied.", {
      timeout: REPLY_TIMEOUT_MS,
    });

    // The proposal shows exactly what would change, and why.
    const proposal = log.getByRole("group", { name: "Proposed edit" });
    await expect(proposal).toBeVisible();
    const criteria = proposal.getByRole("list", { name: "Acceptance criteria" });
    await expect(criteria.getByRole("listitem").filter({ hasText: CRITERION })).toHaveText(
      `Added: ${CRITERION}`,
    );

    await proposal.getByRole("button", { name: "Accept proposed edit" }).click();
    await expect(chat.getByRole("status")).toContainText("Edit accepted.");
    await expect(proposal.getByText(/^Accepted\./)).toBeVisible();
    await expect(proposal.getByRole("button")).toHaveCount(0);
    // The card now carries the written criteria.
    const details = page.getByRole("region", { name: "Details" });
    await expect(details.getByText(CRITERION, { exact: false })).toBeVisible();

    await chat.getByRole("button", { name: "Clear chat" }).click();
    await chat
      .getByRole("alertdialog", { name: "Clear this conversation?" })
      .getByRole("button", {
        name: "Yes, clear",
      })
      .click();
    await expect(log.getByText("No messages yet.")).toBeVisible();
  });
});
