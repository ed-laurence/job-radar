import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, meHandler, problemResponse, renderWithApi, TESTER } from "@/lib/testing";
import { SettingsPage } from "./SettingsPage";

// Typed by the spec, so the mock breaks when the contract changes.
const SETTINGS: Schemas["UserSettings"] = {
  profile: { skills: "Data platforms", brief: "Senior data roles" },
  scoring: { keep_score: 70, borderline_score: 50, notify_score: 85, notify_mission_score: 75 },
  scoring_preferences: {
    weights: { skills: 50, track_pay: 25, location: 15, freshness: 10 },
    pay_floors: { perm_salary_aud: 150000, contract_day_rate_aud: null },
    work_modes: ["remote", "hybrid"],
    preferred_locations: ["Sydney"],
  },
  followups: { after_hours: 72, due_hours: 168, nudge_days: 7 },
  kits: { auto_kits: true, kits_per_run: 3, spelling: "en-AU" },
  campaign: { start_date: "2026-11-02" },
  archive_default_days: 21,
  llm: { monthly_budget_aud: 40 },
  display: { timezone: "Australia/Sydney" },
  version: 4,
  updated_at: "2026-11-20T00:00:00Z",
};

let stored: Schemas["UserSettings"] = SETTINGS;
const puts: Schemas["UserSettingsUpdate"][] = [];

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  stored = SETTINGS;
  puts.length = 0;
  server.use(
    meHandler,
    http.get(`${API}/profile/documents`, () => HttpResponse.json({ items: [] })),
    http.get(`${API}/experience`, () => HttpResponse.json({ items: [] })),
    http.get(`${API}/profile/linkedin-import`, () => HttpResponse.json({ contacts: 0 })),
    http.get(`${API}/settings`, () => HttpResponse.json(stored)),
    http.put(`${API}/settings`, async ({ request }) => {
      const body = (await request.json()) as Schemas["UserSettingsUpdate"];
      puts.push(body);
      if (body.version !== stored.version) {
        return problemResponse(409, "version-conflict", { current_version: stored.version });
      }
      stored = {
        ...body,
        scoring_preferences: body.scoring_preferences ?? stored.scoring_preferences,
        version: body.version + 1,
        updated_at: "2026-11-27T00:00:00Z",
      };
      return HttpResponse.json(stored);
    }),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

async function form() {
  return screen.findByRole("form", { name: "Settings" });
}

describe("SettingsPage", () => {
  it("shows every group, with counters on the profile textareas", async () => {
    renderWithApi(<SettingsPage />);
    const f = await form();
    for (const legend of [
      "Your extra skills",
      "Scoring thresholds",
      "Scoring weights",
      "Pay floors",
      "Work modes and locations",
      "Follow-ups",
      "Kits",
      "Campaign and archive",
      "Display",
      "LLM budget",
    ]) {
      expect(within(f).getByRole("group", { name: legend })).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Skills and experience not in your CVs")).toHaveValue(
      "Data platforms",
    );
    expect(screen.getByText(/Treated as fact alongside your CVs/)).toBeInTheDocument();
    expect(screen.getByLabelText("What you're looking for")).toHaveValue("Senior data roles");
    expect(screen.getByText(`${"Data platforms".length} / 8000 characters`)).toBeInTheDocument();
    expect(
      screen.getByLabelText("Write kits automatically for new high-scoring roles"),
    ).toBeChecked();
    expect(screen.getByLabelText("Time zone")).toHaveValue("Australia/Sydney");
    expect(screen.getByLabelText("Monthly LLM budget (AUD)")).toHaveValue(40);
    expect(screen.queryByText(/search|scan|scrap/i)).toBeNull();
  });

  it("hides the LLM budget from users and sends the stored value back", async () => {
    renderWithApi(<SettingsPage />, { email: TESTER });
    await form();
    expect(screen.queryByRole("group", { name: "LLM budget" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]?.llm.monthly_budget_aud).toBe(40);
  });

  it("refuses keep below borderline, then saves with the version and shows a toast", async () => {
    renderWithApi(<SettingsPage />);
    await form();
    const keep = screen.getByLabelText("Keep score");
    await userEvent.clear(keep);
    await userEvent.type(keep, "40");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByText("Keep score must be at least the borderline score.")).toBeVisible();
    expect(keep).toHaveAttribute("aria-invalid", "true");
    expect(keep).toHaveFocus();
    expect(puts).toHaveLength(0);

    await userEvent.clear(keep);
    await userEvent.type(keep, "65");
    await userEvent.selectOptions(screen.getByLabelText("Time zone"), "__other");
    const zone = screen.getByLabelText("Time zone name");
    await userEvent.clear(zone);
    await userEvent.type(zone, "America/Denver");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(puts[0]).toMatchObject({
      version: 4,
      scoring: { keep_score: 65 },
      display: { timezone: "America/Denver" },
    });
    expect(puts[0]).not.toHaveProperty("updated_at");
  });

  it("on a 409 says it changed elsewhere, reloads, and keeps the edits to re-apply", async () => {
    renderWithApi(<SettingsPage />);
    await form();
    // Someone saves from another tab after this page loaded.
    stored = { ...SETTINGS, archive_default_days: 30, version: 5 };
    const skills = screen.getByLabelText("Skills and experience not in your CVs");
    await userEvent.clear(skills);
    await userEvent.type(skills, "Spark and dbt");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("These settings changed elsewhere");
    expect(within(alert).getByRole("list", { name: "Changed elsewhere" })).toHaveTextContent(
      "Archive older than (days)",
    );
    expect(skills).toHaveValue("Spark and dbt");

    await userEvent.click(screen.getByRole("button", { name: "Save my edits again" }));
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(puts.map((p) => p.version)).toEqual([4, 5]);
    expect(puts[1]?.profile.skills).toBe("Spark and dbt");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("has a section nav whose links move focus to each section", async () => {
    renderWithApi(<SettingsPage />);
    await form();
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "CVs & LinkedIn profile",
      "Your extra skills",
      "Scoring",
      "Follow-ups, kits and display",
      "Experience not on your CV",
      "LinkedIn connections",
    ]);
    for (const link of links) {
      const id = link.getAttribute("href")?.slice(1) ?? "";
      expect(document.getElementById(id)).not.toBeNull();
    }
    await userEvent.click(within(nav).getByRole("link", { name: "Scoring" }));
    expect(screen.getByRole("region", { name: "Scoring" })).toHaveFocus();
  });

  it("checks the weights total live and refuses to save unless it is 100", async () => {
    renderWithApi(<SettingsPage />);
    await form();
    expect(screen.getByText("✓ Total 100 of 100")).toBeInTheDocument();
    const skills = screen.getByLabelText("Skills");
    await userEvent.clear(skills);
    await userEvent.type(skills, "40");
    expect(screen.getByText("Total 90: must total 100")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByText("Weights must total 100; they total 90 now.")).toBeVisible();
    expect(skills).toHaveFocus();
    expect(skills).toHaveAttribute("aria-invalid", "true");
    expect(puts).toHaveLength(0);

    await userEvent.clear(screen.getByLabelText("Freshness"));
    await userEvent.type(screen.getByLabelText("Freshness"), "20");
    expect(screen.getByText("✓ Total 100 of 100")).toBeInTheDocument();
  });

  it("saves pay floors, work modes and preferred locations", async () => {
    renderWithApi(<SettingsPage />);
    await form();
    expect(screen.getByLabelText("Permanent salary floor (AUD a year)")).toHaveValue(150000);
    const rate = screen.getByLabelText("Contract day rate floor (AUD a day)");
    expect(rate).toHaveValue(null);
    await userEvent.type(rate, "900");
    const modes = screen.getByRole("group", { name: "Accepted work modes" });
    expect(within(modes).getByLabelText("Remote")).toBeChecked();
    await userEvent.click(within(modes).getByLabelText("Hybrid"));
    await userEvent.click(within(modes).getByLabelText("On-site"));

    const places = screen.getByLabelText("Preferred locations");
    await userEvent.type(places, "Melbourne{Enter}");
    await userEvent.type(places, "melbourne{Enter}");
    expect(screen.getByText("melbourne is already in the list.")).toBeInTheDocument();
    await userEvent.clear(places);
    await userEvent.click(screen.getByRole("button", { name: "Remove Sydney" }));
    expect(
      within(screen.getByRole("list", { name: "Preferred locations added" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(puts[0]?.scoring_preferences).toEqual({
      weights: { skills: 50, track_pay: 25, location: 15, freshness: 10 },
      pay_floors: { perm_salary_aud: 150000, contract_day_rate_aud: 900 },
      work_modes: ["remote", "on_site"],
      preferred_locations: ["Melbourne"],
    });
  });

  it("caps preferred locations at 10", async () => {
    stored = {
      ...SETTINGS,
      scoring_preferences: {
        ...SETTINGS.scoring_preferences,
        preferred_locations: Array.from({ length: 10 }, (_, i) => `Place ${i + 1}`),
      },
    };
    renderWithApi(<SettingsPage />);
    await form();
    const places = screen.getByLabelText("Preferred locations");
    await userEvent.type(places, "Another{Enter}");
    expect(screen.getByText("You can add up to 10.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to Preferred locations" })).toBeDisabled();
  });

  it("shows a failed load with its reference", async () => {
    server.use(http.get(`${API}/settings`, () => problemResponse(500, "internal")));
    renderWithApi(<SettingsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("corr-test-0001");
  });
});
