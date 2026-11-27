import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problemResponse, renderWithApi } from "@/lib/testing";
import { SavedKit } from "./SavedKit";

const ROLE_ID = "00000000-0000-4000-8000-0000000000a1";
const kit: Schemas["Kit"] = {
  cv: "Lead with streaming work.",
  cover_note: "Dear Hiring Team,\n\nI am applying…",
  generated_at: "2026-11-27T10:00:00Z",
  version: 1,
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const pdf = (name: string) =>
  new HttpResponse(new TextEncoder().encode("%PDF-1.7"), {
    headers: {
      "content-type": "application/pdf",
      "content-disposition": `attachment; filename="${name}"`,
    },
  });

let clicked: { href: string; download: string }[];
beforeEach(() => {
  clicked = [];
  Object.defineProperty(URL, "createObjectURL", {
    value: vi.fn(() => "blob:kit"),
    configurable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicked.push({ href: this.href, download: this.download });
  });
});

describe("SavedKit downloads", () => {
  it("downloads the cover letter and the CV as PDFs named by the server", async () => {
    server.use(
      http.get(`${API}/roles/:roleId/kit/cover-letter.pdf`, () =>
        pdf("Alex_Example_Cover_Letter_Saltbush.pdf"),
      ),
      http.get(`${API}/roles/:roleId/kit/cv.pdf`, () => pdf("Alex_Example_CV.pdf")),
    );
    renderWithApi(<SavedKit kit={kit} roleId={ROLE_ID} />);
    await userEvent.click(screen.getByRole("button", { name: "Download cover letter (PDF)" }));
    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(clicked[0]).toEqual({
      href: "blob:kit",
      download: "Alex_Example_Cover_Letter_Saltbush.pdf",
    });
    await userEvent.click(screen.getByRole("button", { name: "Download CV (PDF)" }));
    await waitFor(() => expect(clicked).toHaveLength(2));
    expect(clicked[1]?.download).toBe("Alex_Example_CV.pdf");
  });

  it("hides the letter download when there is no cover letter", () => {
    renderWithApi(<SavedKit kit={{ ...kit, cover_note: undefined }} roleId={ROLE_ID} />);
    expect(screen.queryByRole("button", { name: "Download cover letter (PDF)" })).toBeNull();
    expect(screen.getByRole("button", { name: "Download CV (PDF)" })).toBeInTheDocument();
  });

  it("explains a missing CV instead of failing silently", async () => {
    server.use(
      http.get(`${API}/roles/:roleId/kit/cv.pdf`, () =>
        problemResponse(404, "not-found", { detail: "Upload a CV in Settings first." }),
      ),
    );
    renderWithApi(<SavedKit kit={kit} roleId={ROLE_ID} />);
    await userEvent.click(screen.getByRole("button", { name: "Download CV (PDF)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload a CV in Settings");
    expect(clicked).toHaveLength(0);
  });
});
