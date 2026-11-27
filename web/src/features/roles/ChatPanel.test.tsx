import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { API, gatedStream, renderWithApi, SSE_HEADERS, sseFrame } from "@/lib/testing";
import { ChatPanel } from "./ChatPanel";
import { ROLE_ID } from "./testing";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const msg = (id: string, author: "user" | "assistant", content: string) => ({
  id: `00000000-0000-4000-8000-0000000001${id}`,
  author,
  content,
  created_at: "2026-11-22T00:00:00Z",
});

describe("ChatPanel", () => {
  it("lists history and streams a reply, then reloads the saved thread", async () => {
    let history = [msg("01", "user", "Hi"), msg("02", "assistant", "Hello **there**")];
    let release = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let body: unknown;
    server.use(
      http.get(`${API}/roles/:roleId/chat/messages`, () => HttpResponse.json({ items: history })),
      http.post(`${API}/roles/:roleId/chat/messages`, async ({ request }) => {
        body = await request.json();
        return new HttpResponse(
          gatedStream(
            [
              sseFrame("segment", "0", {
                segment_id: "a",
                seq: 0,
                part: "chat",
                text: "Lead with ",
              }),
            ],
            gate,
            [
              sseFrame("segment", "1", { segment_id: "b", seq: 1, part: "chat", text: "impact." }),
              sseFrame("done", "d", {}),
            ],
          ),
          { headers: SSE_HEADERS },
        );
      }),
    );
    renderWithApi(<ChatPanel roleId={ROLE_ID} />);
    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(within(log).getByText("there").tagName).toBe("STRONG");

    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "What to stress?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await within(log).findByText(/Lead with/)).toBeInTheDocument();
    expect(within(log).getByText("What to stress?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop reply" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("");

    history = [
      ...history,
      msg("03", "user", "What to stress?"),
      msg("04", "assistant", "Lead with impact."),
    ];
    await act(async () => release());
    expect(await within(log).findByText("Lead with impact.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Stop reply" })).not.toBeInTheDocument(),
    );
    expect(body).toEqual({ content: "What to stress?" });
  });

  it("clears the conversation after confirming", async () => {
    let cleared = false;
    server.use(
      http.get(`${API}/roles/:roleId/chat/messages`, () =>
        HttpResponse.json({ items: cleared ? [] : [msg("01", "user", "Hi")] }),
      ),
      http.delete(`${API}/roles/:roleId/chat/messages`, () => {
        cleared = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApi(<ChatPanel roleId={ROLE_ID} />);
    expect(await screen.findByText("Hi")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear chat" }));
    await userEvent.click(
      within(screen.getByRole("alertdialog", { name: "Clear this conversation?" })).getByRole(
        "button",
        { name: "Yes, clear" },
      ),
    );
    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
    expect(cleared).toBe(true);
  });
});
