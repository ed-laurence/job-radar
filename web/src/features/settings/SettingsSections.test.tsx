import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, meHandler, problemResponse, renderWithApi } from "@/lib/testing";
import { ConnectionsSection } from "./ConnectionsSection";
import { DocumentsSection } from "./DocumentsSection";
import { ExperienceSection } from "./ExperienceSection";

// Typed by the spec, so the mocks break when the contract changes.
const TECH_CV: Schemas["ProfileDocument"] = {
  slot: "technical",
  filename: "technical-cv.pdf",
  uploaded_at: "2026-11-20T00:00:00Z",
  pages: 2,
  size_bytes: 120 * 1024,
  readability: { sections: 5, roles: 4, bullets: 22, ok: true },
  archived_versions: 1,
};
const LEAD_CV: Schemas["ProfileDocument"] = {
  ...TECH_CV,
  slot: "leadership",
  filename: "leadership-cv.pdf",
  readability: { sections: 3, roles: 0, bullets: 0, ok: false },
  archived_versions: 0,
};

const RECORD: Schemas["ExperienceRecord"] = {
  id: "00000000-0000-4000-8000-0000000000e1",
  ref: "E1",
  requirement: "Reporting pipelines",
  entries: [
    { ref: "E1.1", context: "Data Lead, Example Co", detail: "Built a small reporting pipeline." },
    {
      ref: "E1.2",
      context: "Volunteer, Example Community Group",
      detail: "Ran the monthly report.",
    },
  ],
  uses: 3,
  version: 2,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};
/** "Volunteer, Example Community Group" isn't suggested any more, so it must stay selectable. */
const CONTEXTS = ["Data Lead, Example Co", "Analyst, Example Bank", "Personal project", "Other"];

/** What the server stores: the entries get their refs. */
function stored(
  base: Schemas["ExperienceRecord"],
  body: Schemas["ExperienceCreate"] | Schemas["ExperienceUpdate"],
): Schemas["ExperienceRecord"] {
  return {
    ...base,
    requirement: body.requirement,
    entries: body.entries.map((e, k) => ({ ...e, ref: `${base.ref}.${k + 1}` })),
  };
}

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => server.use(meHandler));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const user = () => userEvent.setup({ applyAccept: false });

function pdf(name = "new-cv.pdf", size?: number): File {
  const file = new File(["%PDF-1.7 fake"], name, { type: "application/pdf" });
  if (size !== undefined) Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("CVs & LinkedIn profile", () => {
  let docs: Schemas["ProfileDocument"][];
  const uploads: { slot: string; body: Schemas["ProfileDocumentUpload"] }[] = [];
  const deletes: string[] = [];

  beforeEach(() => {
    docs = [TECH_CV, LEAD_CV];
    uploads.length = 0;
    deletes.length = 0;
    server.use(
      http.get(`${API}/profile/documents`, () => HttpResponse.json({ items: docs })),
      http.put(`${API}/profile/documents/:slot`, async ({ request, params }) => {
        const body = (await request.json()) as Schemas["ProfileDocumentUpload"];
        uploads.push({ slot: String(params.slot), body });
        const saved: Schemas["ProfileDocument"] = {
          ...TECH_CV,
          slot: params.slot as Schemas["DocumentSlot"],
          filename: body.filename,
        };
        docs = [...docs.filter((d) => d.slot !== saved.slot), saved];
        return HttpResponse.json(saved);
      }),
      http.delete(`${API}/profile/documents/:slot`, ({ params }) => {
        deletes.push(String(params.slot));
        docs = docs.filter((d) => d.slot !== params.slot);
        return new HttpResponse(null, { status: 204 });
      }),
    );
  });

  it("shows one card per slot with details and the readability check", async () => {
    renderWithApi(<DocumentsSection timeZone="UTC" />);
    const tech = await screen.findByRole("article", { name: "Technical CV" });
    expect(within(tech).getByText("technical-cv.pdf")).toBeInTheDocument();
    expect(within(tech).getByText(/2 pages · 120 KB/)).toBeInTheDocument();
    expect(
      within(tech).getByText("✓ Readable: 5 sections, 4 roles and 22 bullets found"),
    ).toBeInTheDocument();
    expect(within(tech).getByText("1 earlier version is kept.")).toBeInTheDocument();
    const lead = screen.getByRole("article", { name: "Leadership CV" });
    expect(within(lead).getByText(/Hard to read: no roles, bullets found/)).toBeInTheDocument();
    const li = screen.getByRole("article", { name: "LinkedIn profile PDF" });
    expect(within(li).getByText("Not uploaded.")).toBeInTheDocument();
    expect(within(li).getByRole("button", { name: "Upload… LinkedIn profile PDF" })).toBeVisible();
    expect(screen.getByText(/Scoring reads your CV text/)).toBeInTheDocument();
  });

  it("refuses a non-PDF or an oversized file without sending it", async () => {
    const u = user();
    renderWithApi(<DocumentsSection timeZone="UTC" />);
    await screen.findByRole("article", { name: "Technical CV" });
    await u.upload(
      screen.getByTestId("file-Technical CV"),
      new File(["x"], "cv.docx", { type: "application/msword" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PDF file (.pdf).");
    await u.upload(screen.getByTestId("file-Technical CV"), pdf("big.pdf", 6 * 1024 * 1024));
    expect(await screen.findByRole("alert")).toHaveTextContent("the limit is 5 MB");
    expect(uploads).toHaveLength(0);
  });

  it("uploads a PDF as base64 JSON and announces it", async () => {
    const u = user();
    renderWithApi(<DocumentsSection timeZone="UTC" />);
    await screen.findByRole("article", { name: "LinkedIn profile PDF" });
    await u.upload(screen.getByTestId("file-LinkedIn profile PDF"), pdf("Profile.pdf"));
    expect(await screen.findByText("LinkedIn profile PDF uploaded.")).toBeInTheDocument();
    expect(uploads[0]?.slot).toBe("linkedin_profile");
    expect(uploads[0]?.body.filename).toBe("Profile.pdf");
    expect(atob(uploads[0]?.body.data ?? "")).toBe("%PDF-1.7 fake");
    const li = screen.getByRole("article", { name: "LinkedIn profile PDF" });
    expect(await within(li).findByText("Profile.pdf")).toBeInTheDocument();
  });

  it("shows a server rejection on the card", async () => {
    server.use(
      http.put(`${API}/profile/documents/:slot`, () =>
        problemResponse(422, "validation", { title: "That PDF is encrypted" }),
      ),
    );
    const u = user();
    renderWithApi(<DocumentsSection timeZone="UTC" />);
    await screen.findByRole("article", { name: "Technical CV" });
    await u.upload(screen.getByTestId("file-Technical CV"), pdf());
    const card = screen.getByRole("article", { name: "Technical CV" });
    expect(await within(card).findByRole("alert")).toHaveTextContent("That PDF is encrypted");
  });

  it("removes a document only after confirming", async () => {
    const u = user();
    renderWithApi(<DocumentsSection timeZone="UTC" />);
    const tech = await screen.findByRole("article", { name: "Technical CV" });
    await u.click(within(tech).getByRole("button", { name: "Remove Technical CV" }));
    const dialog = screen.getByRole("alertdialog", { name: "Remove your Technical CV?" });
    await u.keyboard("{Escape}");
    expect(dialog).not.toBeInTheDocument();
    expect(deletes).toHaveLength(0);
    await u.click(within(tech).getByRole("button", { name: "Remove Technical CV" }));
    await u.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByText("Technical CV removed.")).toBeInTheDocument();
    expect(deletes).toEqual(["technical"]);
    expect(
      await within(screen.getByRole("article", { name: "Technical CV" })).findByText(
        "Not uploaded.",
      ),
    ).toBeInTheDocument();
  });

  it("opens the PDF in a new tab from the authenticated download", async () => {
    server.use(
      http.get(
        `${API}/profile/documents/:slot/file`,
        () =>
          new HttpResponse(new TextEncoder().encode("%PDF-1.7"), {
            headers: { "content-type": "application/pdf" },
          }),
      ),
    );
    const tab = { opener: {} as unknown, location: { href: "" }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const created = vi.fn(() => "blob:test-pdf");
    Object.defineProperty(URL, "createObjectURL", { value: created, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
    const u = user();
    renderWithApi(<DocumentsSection timeZone="UTC" />);
    await u.click(
      await screen.findByRole("button", { name: "View Technical CV (opens in a new tab)" }),
    );
    expect(open).toHaveBeenCalledWith("", "_blank");
    await waitFor(() => expect(tab.location.href).toBe("blob:test-pdf"));
    expect(tab.opener).toBeNull();
    expect(created).toHaveBeenCalledTimes(1);
  });
});

describe("Experience not on your CV", () => {
  let records: Schemas["ExperienceRecord"][];
  const puts: Schemas["ExperienceUpdate"][] = [];
  const posts: Schemas["ExperienceCreate"][] = [];

  beforeEach(() => {
    records = [RECORD];
    puts.length = 0;
    posts.length = 0;
    server.use(
      http.get(`${API}/experience`, () =>
        HttpResponse.json({ items: records, contexts: CONTEXTS }),
      ),
      http.post(`${API}/experience`, async ({ request }) => {
        const body = (await request.json()) as Schemas["ExperienceCreate"];
        posts.push(body);
        const saved = stored(
          { ...RECORD, id: "00000000-0000-4000-8000-0000000000e2", ref: "E2", uses: 0, version: 1 },
          body,
        );
        records = [...records, saved];
        return HttpResponse.json(saved, { status: 201 });
      }),
      http.put(`${API}/experience/:id`, async ({ request, params }) => {
        const body = (await request.json()) as Schemas["ExperienceUpdate"];
        puts.push(body);
        const current = records.find((r) => r.id === params.id);
        if (!current) return problemResponse(404, "not-found");
        if (body.version !== current.version) {
          return problemResponse(409, "version-conflict", { current_version: current.version });
        }
        const saved = { ...stored(current, body), version: current.version + 1 };
        records = records.map((r) => (r.id === saved.id ? saved : r));
        return HttpResponse.json(saved);
      }),
      http.delete(`${API}/experience/:id`, ({ request }) => {
        const version = new URL(request.url).searchParams.get("version");
        if (version !== String(records[0]?.version))
          return problemResponse(409, "version-conflict");
        records = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );
  });

  async function record(name = "Reporting pipelines") {
    return screen.findByRole("form", { name });
  }

  function rows(form: HTMLElement) {
    return within(form).getAllByRole("group", { name: /^Entry \d+$/ });
  }

  it("lists each record with its ref, use count and entries, editable in place", async () => {
    renderWithApi(<ExperienceSection />);
    const form = await record();
    expect(
      screen.getByRole("heading", { level: 2, name: "Experience not on your CV (1)" }),
    ).toBeVisible();
    expect(screen.getByText(/treated as fact/)).toBeInTheDocument();
    expect(within(form).getByRole("textbox", { name: "Requirement it answers" })).toHaveValue(
      "Reporting pipelines",
    );
    expect(within(form).getByText("E1 · matched in 3 ads")).toBeVisible();
    expect(within(form).getByText("worth adding to your base CV")).toBeVisible();

    const [first, second] = rows(form) as [HTMLElement, HTMLElement];
    const where = within(first).getByRole("combobox", { name: "Where" });
    expect(where).toHaveValue("Data Lead, Example Co");
    expect(
      within(where)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Choose the role or project…", ...CONTEXTS]);
    expect(within(first).getByRole("textbox", { name: "What you did there" })).toHaveValue(
      "Built a small reporting pipeline.",
    );
    // A saved context that's no longer suggested stays selected (and selectable).
    expect(within(second).getByRole("combobox", { name: "Where" })).toHaveValue(
      "Volunteer, Example Community Group",
    );
    expect(within(first).getByRole("button", { name: "Remove entry 1" })).toBeEnabled();
    expect(within(form).getByRole("button", { name: "+ Another role" })).toBeEnabled();
    expect(within(form).getByRole("button", { name: "Save" })).toBeEnabled();
    expect(within(form).getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("leaves out the use count and the hint when a record isn't used much", async () => {
    records = [{ ...RECORD, uses: 0 }];
    renderWithApi(<ExperienceSection />);
    const form = await record();
    expect(within(form).getByText("E1")).toBeVisible();
    expect(within(form).queryByText(/matched in/)).toBeNull();
    expect(within(form).queryByText("worth adding to your base CV")).toBeNull();
  });

  it("says None yet when there are no records", async () => {
    records = [];
    renderWithApi(<ExperienceSection />);
    expect(await screen.findByText("None yet.")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "Experience not on your CV (0)" }),
    ).toBeVisible();
  });

  it("validates, then adds a record and clears the form", async () => {
    const u = user();
    renderWithApi(<ExperienceSection />);
    const form = await screen.findByRole("form", { name: "Add one" });
    const requirement = within(form).getByRole("textbox", { name: "Requirement it answers" });
    expect(requirement).toHaveAttribute(
      "placeholder",
      "e.g. dbt, stakeholder management at board level",
    );
    const add = within(form).getByRole("button", { name: "Add" });

    await u.click(add);
    expect(requirement).toHaveFocus();
    expect(requirement).toHaveAttribute("aria-invalid", "true");
    expect(within(form).getByText("Say which requirement it answers")).toBeVisible();

    await u.type(requirement, "Mentoring");
    await u.click(add);
    expect(within(form).getByText("Add at least one role and what you did there")).toBeVisible();
    const [row] = rows(form) as [HTMLElement];
    expect(within(row).getByRole("combobox", { name: "Where" })).toHaveFocus();

    const detail = within(row).getByRole("textbox", { name: "What you did there" });
    expect(detail).toHaveAttribute(
      "placeholder",
      "Specific and true; numbers only if they're real",
    );
    await u.type(detail, "Coached two junior analysts.");
    await u.click(add);
    expect(within(form).getByText("Choose where you did it for each row")).toBeVisible();
    const where = within(row).getByRole("combobox", { name: "Where" });
    expect(where).toHaveFocus();
    expect(where).toHaveAttribute("aria-invalid", "true");
    await u.selectOptions(where, "Analyst, Example Bank");

    await u.click(within(form).getByRole("button", { name: "+ Another role" }));
    const second = rows(form)[1] as HTMLElement;
    expect(within(second).getByRole("combobox", { name: "Where" })).toHaveFocus();
    await u.selectOptions(within(second).getByRole("combobox", { name: "Where" }), "Other");
    await u.click(add);
    expect(within(form).getByText("Say what you did in each role")).toBeVisible();
    expect(within(second).getByRole("textbox", { name: "What you did there" })).toHaveFocus();

    await u.click(within(second).getByRole("button", { name: "Remove entry 2" }));
    expect(rows(form)).toHaveLength(1);
    await u.click(add);
    expect(await screen.findByText("Added Mentoring.")).toBeInTheDocument();
    expect(posts).toEqual([
      {
        requirement: "Mentoring",
        entries: [{ context: "Analyst, Example Bank", detail: "Coached two junior analysts." }],
      },
    ]);
    expect(await record("Mentoring")).toBeInTheDocument();
    const fresh = screen.getByRole("form", { name: "Add one" });
    expect(within(fresh).getByRole("textbox", { name: "Requirement it answers" })).toHaveValue("");
    expect(within(fresh).getByRole("combobox", { name: "Where" })).toHaveValue("");
  });

  it("saves an edited record in place, dropping blank rows", async () => {
    const u = user();
    renderWithApi(<ExperienceSection />);
    const form = await record();
    const requirement = within(form).getByRole("textbox", { name: "Requirement it answers" });
    await u.clear(requirement);
    await u.type(requirement, "Reporting pipelines and dashboards");
    await u.click(within(form).getByRole("button", { name: "+ Another role" }));
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(
      await screen.findByText("Saved Reporting pipelines and dashboards."),
    ).toBeInTheDocument();
    expect(puts).toEqual([
      {
        requirement: "Reporting pipelines and dashboards",
        entries: RECORD.entries.map(({ context, detail }) => ({ context, detail })),
        version: 2,
      },
    ]);
  });

  it("on a 409 keeps the edits, takes the latest version and saves again", async () => {
    const u = user();
    renderWithApi(<ExperienceSection />);
    const form = await record();
    // Someone else saves it after the edit started.
    records = [{ ...RECORD, version: 3 }];
    const detail = within(rows(form)[0] as HTMLElement).getByRole("textbox", {
      name: "What you did there",
    });
    await u.clear(detail);
    await u.type(detail, "Built the reporting pipeline.");
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "This record changed elsewhere",
    );
    expect(detail).toHaveValue("Built the reporting pipeline.");
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved Reporting pipelines.")).toBeInTheDocument();
    expect(puts.map((p) => p.version)).toEqual([2, 3]);
    expect(puts[1]?.entries[0]?.detail).toBe("Built the reporting pipeline.");
  });

  it("says so when the record was deleted elsewhere", async () => {
    const u = user();
    renderWithApi(<ExperienceSection />);
    const form = await record();
    records = [];
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "It was deleted elsewhere, so your edit wasn't saved.",
    );
    expect(await screen.findByText("None yet.")).toBeInTheDocument();
  });

  it("validates an edited record before saving", async () => {
    const u = user();
    renderWithApi(<ExperienceSection />);
    const form = await record();
    for (const row of rows(form)) {
      await u.clear(within(row).getByRole("textbox", { name: "What you did there" }));
    }
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(within(form).getByText("Say what you did in each role")).toBeVisible();
    expect(puts).toHaveLength(0);
  });

  it("deletes after confirming, sending the version", async () => {
    const u = user();
    renderWithApi(<ExperienceSection />);
    const form = await record();
    const del = within(form).getByRole("button", { name: "Delete" });
    await u.click(del);
    const confirm = screen.getByRole("alertdialog", { name: "Delete Reporting pipelines?" });
    await u.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(del).toHaveFocus();
    await u.click(del);
    await u.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Deleted Reporting pipelines.")).toBeInTheDocument();
    expect(await screen.findByText("None yet.")).toBeInTheDocument();
  });
});

describe("LinkedIn connections", () => {
  let summary: Schemas["LinkedinImportSummary"];
  const imports: Schemas["ContactsImportRequest"][] = [];
  const removals: Schemas["ContactsDeleteRequest"][] = [];

  beforeEach(() => {
    summary = {
      contacts: 120,
      last: {
        filename: "Connections.csv",
        imported_at: "2026-11-10T00:00:00Z",
        imported: 118,
        updated: 0,
        skipped: 2,
      },
    };
    imports.length = 0;
    removals.length = 0;
    server.use(
      http.get(`${API}/profile/linkedin-import`, () => HttpResponse.json(summary)),
      http.get(`${API}/contacts`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post(`${API}/contacts/import`, async ({ request }) => {
        const body = (await request.json()) as Schemas["ContactsImportRequest"];
        imports.push(body);
        const result = { imported: 3, updated: 117, skipped: 1 };
        summary = {
          contacts: 123,
          last: { filename: body.filename, imported_at: "2026-11-27T00:00:00Z", ...result },
        };
        return HttpResponse.json(result);
      }),
      http.delete(`${API}/contacts`, async ({ request }) => {
        removals.push((await request.json()) as Schemas["ContactsDeleteRequest"]);
        const deleted = summary.contacts;
        summary = { contacts: 0, ...(summary.last ? { last: summary.last } : {}) };
        return HttpResponse.json({ deleted });
      }),
    );
  });

  it("shows the export steps and the last import", async () => {
    renderWithApi(<ConnectionsSection timeZone="UTC" />);
    expect(
      await screen.findByText(/Last import .*118 imported, 0 updated, 2 skipped/),
    ).toBeVisible();
    expect(screen.getByText(/You have 120 contacts/)).toBeVisible();
    expect(screen.getByText(/Get a copy of your data/)).toBeVisible();
    expect(screen.getAllByText("Connections.csv").length).toBeGreaterThan(0);
  });

  it("asks for a file, refuses a big one, then imports and shows the summary", async () => {
    const u = user();
    renderWithApi(<ConnectionsSection timeZone="UTC" />);
    const form = await screen.findByRole("form", { name: "Import connections" });
    await u.click(within(form).getByRole("button", { name: "Import contacts" }));
    expect(within(form).getByText("Choose your Connections.csv first.")).toBeVisible();

    const input = within(form).getByLabelText("Connections.csv");
    const big = new File(["a"], "Connections.csv", { type: "text/csv" });
    Object.defineProperty(big, "size", { value: 3 * 1024 * 1024 });
    await u.upload(input, big);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(within(form).getByText(/the limit is 2 MB/)).toBeVisible();

    await u.upload(
      input,
      new File(["First Name,Last Name\nAda,Example\n"], "Connections.csv", { type: "text/csv" }),
    );
    await u.click(within(form).getByRole("button", { name: "Import contacts" }));
    expect(
      await within(form).findByText("Import finished: 3 imported, 117 updated, 1 skipped."),
    ).toBeVisible();
    expect(atob(imports[0]?.data ?? "")).toBe("First Name,Last Name\nAda,Example\n");
    expect(await screen.findByText(/You have 123 contacts/)).toBeVisible();
  });

  it("removes all contacts only after the typed confirmation", async () => {
    const u = user();
    renderWithApi(<ConnectionsSection timeZone="UTC" />);
    await u.click(await screen.findByRole("button", { name: "Remove all contacts…" }));
    const confirm = screen.getByLabelText('Type "remove all" to confirm');
    expect(confirm).toHaveFocus();
    const go = screen.getByRole("button", { name: "Remove all 120 contacts" });
    expect(go).toBeDisabled();
    await u.type(confirm, "remove al");
    expect(go).toBeDisabled();
    await u.type(confirm, "l");
    await u.click(go);
    expect(await screen.findByText("Removed 120 contacts.")).toBeInTheDocument();
    expect(removals).toEqual([{ confirm: true }]);
    expect(await screen.findByText(/You have 0 contacts/)).toBeVisible();
  });

  it("announces a failed import", async () => {
    server.use(
      http.post(`${API}/contacts/import`, () =>
        problemResponse(422, "validation", { title: "That isn't a LinkedIn connections file" }),
      ),
    );
    const u = user();
    renderWithApi(<ConnectionsSection timeZone="UTC" />);
    const form = await screen.findByRole("form", { name: "Import connections" });
    await u.upload(
      within(form).getByLabelText("Connections.csv"),
      new File(["x"], "Connections.csv", { type: "text/csv" }),
    );
    await u.click(within(form).getByRole("button", { name: "Import contacts" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "That isn't a LinkedIn connections file",
    );
  });
});
