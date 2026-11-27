import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { API, problem, renderWithApi } from "@/lib/testing";
import { RoleDetailPage } from "./RoleDetailPage";
import { makeRole, ROLE_ID } from "./testing";

const router = { push: vi.fn(), replace: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => `/roles/${ROLE_ID}`,
}));

let current = makeRole();
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  current = makeRole();
  server.use(
    http.get(`${API}/roles/:roleId`, () => HttpResponse.json(current)),
    http.get(`${API}/roles/:roleId/chat/messages`, () => HttpResponse.json({ items: [] })),
    http.get(`${API}/targets`, () => HttpResponse.json({ items: [] })),
    // Never checked: the evidence panel offers the first run.
    http.get(`${API}/roles/:roleId/evidence`, () =>
      HttpResponse.json(problem(404, "not-found"), { status: 404 }),
    ),
  );
});
afterEach(() => {
  server.resetHandlers();
  router.push.mockReset();
});
afterAll(() => server.close());

describe("RoleDetailPage", () => {
  it("shows the role and treats the ad text as plain text", async () => {
    current = makeRole({ description: "<b>Bold</b> claim", why: "Strong **fit**" });
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Senior Data Engineer" }),
    ).toBeInTheDocument();
    expect(screen.getByText("<b>Bold</b> claim")).toBeInTheDocument();
    expect(screen.getByText("fit").tagName).toBe("STRONG");
  });

  it("shows not found for a missing role", async () => {
    server.use(
      http.get(`${API}/roles/:roleId`, () =>
        HttpResponse.json(problem(404, "not-found"), { status: 404 }),
      ),
    );
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Role not found");
  });

  it("edits with the read version", async () => {
    let body: unknown;
    server.use(
      http.patch(`${API}/roles/:roleId`, async ({ request }) => {
        body = await request.json();
        current = makeRole({ notes: "Ask about on-call", version: 4 });
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit role" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Notes" }), "Ask about on-call");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Ask about on-call")).toBeInTheDocument();
    expect(body).toEqual({ version: 3, notes: "Ask about on-call" });
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
  });

  it("on a 409 shows the latest version and retries with it", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.patch(`${API}/roles/:roleId`, async ({ request }) => {
        const body = (await request.json()) as { version: number };
        bodies.push(body);
        if (body.version === 3) {
          current = makeRole({ company: "Acme Holdings", version: 5 });
          return HttpResponse.json(problem(409, "version-conflict", { current_version: 5 }), {
            status: 409,
          });
        }
        current = makeRole({ company: "Acme Holdings", title: "Staff Data Engineer", version: 6 });
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit role" }));
    const title = screen.getByRole("textbox", { name: "Title" });
    await userEvent.clear(title);
    await userEvent.type(title, "Staff Data Engineer");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/changed since you opened it/i);
    expect(await within(alert).findByText(/Acme Holdings/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Staff Data Engineer");

    await userEvent.click(within(alert).getByRole("button", { name: "Save my changes again" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Staff Data Engineer" }),
    ).toBeInTheDocument();
    expect(bodies).toEqual([
      { version: 3, title: "Staff Data Engineer" },
      { version: 5, title: "Staff Data Engineer" },
    ]);
  });

  it("holds a status change until a pending edit has saved, then sends the new version", async () => {
    let release: () => void = () => {};
    const saved = new Promise<void>((resolve) => {
      release = resolve;
    });
    let body: unknown;
    server.use(
      http.patch(`${API}/roles/:roleId`, async () => {
        await saved;
        current = makeRole({ notes: "Edited", version: 4 });
        return HttpResponse.json(current);
      }),
      http.post(`${API}/roles/:roleId/status`, async ({ request }) => {
        body = await request.json();
        current = makeRole({ notes: "Edited", status: "applied", version: 5 });
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit role" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Notes" }), "Edited");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Status" }), "applied");
    const update = screen.getByRole("button", { name: "Update status" });
    expect(update).toBeDisabled();

    release();
    await waitFor(() => expect(update).toBeEnabled());
    await userEvent.click(update);
    expect(await screen.findByRole("status", { name: "Status update" })).toHaveTextContent(
      "Status changed to Applied",
    );
    expect(body).toEqual({ status: "applied", version: 4 });
  });

  it("changes status, with rejection reasons", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/roles/:roleId/status`, async ({ request }) => {
        body = await request.json();
        current = makeRole({ status: "rejected", version: 4 });
        return HttpResponse.json(current);
      }),
    );
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    const select = await screen.findByRole("combobox", { name: "Status" });
    await userEvent.selectOptions(select, "rejected");
    await userEvent.click(screen.getByRole("checkbox", { name: "Seniority" }));
    await userEvent.click(screen.getByRole("button", { name: "Update status" }));

    expect(await screen.findByRole("status", { name: "Status update" })).toHaveTextContent(
      "Status changed to Rejected",
    );
    expect(body).toEqual({
      status: "rejected",
      version: 3,
      rejection: { reason_categories: ["seniority"] },
    });
  });

  it("soft deletes only after confirming", async () => {
    let deletedWith: string | null = null;
    server.use(
      http.delete(`${API}/roles/:roleId`, ({ request }) => {
        deletedWith = new URL(request.url).searchParams.get("version");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApi(<RoleDetailPage roleId={ROLE_ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete role" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this role?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(deletedWith).toBeNull();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete role" }));
    const confirm = within(screen.getByRole("alertdialog")).getByRole("button", {
      name: "Yes, delete",
    });
    expect(confirm).toHaveFocus();
    await userEvent.click(confirm);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/roles"));
    expect(deletedWith).toBe("3");
  });
});
