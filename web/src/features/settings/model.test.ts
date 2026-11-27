import { describe, expect, it } from "vitest";
import { changedFields, type Settings, toDraft, toUpdate, validate, weightTotal } from "./model";

const SETTINGS: Settings = {
  profile: { skills: "Data platforms", brief: "Senior data roles in Sydney" },
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

describe("settings model", () => {
  it("round-trips the settings and sends the version it was read at", () => {
    const body = toUpdate(toDraft(SETTINGS), SETTINGS, { maintainer: true });
    const { updated_at, ...rest } = SETTINGS;
    expect(updated_at).toBeTruthy();
    expect(body).toEqual(rest);
  });

  it("accepts the stored settings", () => {
    expect(validate(toDraft(SETTINGS), { maintainer: true })).toEqual({});
  });

  it("requires keep to be at least borderline", () => {
    const d = { ...toDraft(SETTINGS), keep_score: "40" };
    expect(validate(d, { maintainer: false }).keep_score).toBe(
      "Keep score must be at least the borderline score.",
    );
    expect(validate({ ...d, keep_score: "50" }, { maintainer: false })).toEqual({});
  });

  it("checks ranges, whole numbers, the time zone and the budget", () => {
    const d = {
      ...toDraft(SETTINGS),
      nudge_days: "61",
      kits_per_run: "2.5",
      archive_default_days: "",
      timezone: "Nowhere/Here",
      monthly_budget_aud: "-1",
    };
    const errors = validate(d, { maintainer: true });
    expect(Object.keys(errors).sort()).toEqual([
      "archive_default_days",
      "kits_per_run",
      "monthly_budget_aud",
      "nudge_days",
      "timezone",
    ]);
    expect(validate(d, { maintainer: false }).monthly_budget_aud).toBeUndefined();
    expect(validate(d, { maintainer: true }).monthly_budget_aud).toBe(
      "Budget must be from 0 to 10000 AUD, or blank for no cap.",
    );
  });

  it("treats a null or missing budget as no cap, never as text", () => {
    const none = { ...SETTINGS, llm: { monthly_budget_aud: null } };
    expect(toDraft(none).monthly_budget_aud).toBe("");
    expect(validate(toDraft(none), { maintainer: true })).toEqual({});
    expect(toUpdate(toDraft(none), none, { maintainer: false }).llm.monthly_budget_aud).toBeNull();
    // An older or partial response without the field (or without `llm`) must not break the page.
    const missing = { ...SETTINGS, llm: {} } as unknown as Settings;
    const noLlm = { ...SETTINGS, llm: undefined } as unknown as Settings;
    for (const s of [missing, noLlm]) {
      expect(toDraft(s).monthly_budget_aud).toBe("");
      expect(validate(toDraft(s), { maintainer: true })).toEqual({});
      expect(toUpdate(toDraft(s), s, { maintainer: false }).llm.monthly_budget_aud).toBeNull();
    }
  });

  it("sends blanks as null, and users send the stored budget back", () => {
    const d = { ...toDraft(SETTINGS), start_date: "", monthly_budget_aud: "" };
    expect(toUpdate(d, SETTINGS, { maintainer: true }).campaign.start_date).toBeNull();
    expect(toUpdate(d, SETTINGS, { maintainer: true }).llm.monthly_budget_aud).toBeNull();
    expect(toUpdate(d, SETTINGS, { maintainer: false }).llm.monthly_budget_aud).toBe(40);
  });

  it("requires the weights to total 100", () => {
    const d = { ...toDraft(SETTINGS), weight_skills: "45" };
    expect(weightTotal(d)).toBe(95);
    expect(validate(d, { maintainer: true }).weights).toBe(
      "Weights must total 100; they total 95 now.",
    );
    const bad = { ...toDraft(SETTINGS), weight_location: "abc" };
    expect(validate(bad, { maintainer: true }).weight_location).toMatch(/whole number/);
    expect(validate(bad, { maintainer: true }).weights).toBeUndefined();
  });

  it("checks pay floors and locations, and sends blank floors as null", () => {
    const d = { ...toDraft(SETTINGS), perm_salary_aud: "2000001", contract_day_rate_aud: "1.5" };
    const errors = validate(d, { maintainer: true });
    expect(errors.perm_salary_aud).toMatch(/0 to 2,000,000/);
    expect(errors.contract_day_rate_aud).toMatch(/0 to 20,000/);
    const many = { ...toDraft(SETTINGS), preferred_locations: Array(11).fill("x") };
    expect(validate(many, { maintainer: true }).preferred_locations).toBe(
      "Keep it to 10 locations.",
    );
    const blank = { ...toDraft(SETTINGS), perm_salary_aud: " " };
    expect(toUpdate(blank, SETTINGS, { maintainer: true }).scoring_preferences?.pay_floors).toEqual(
      { perm_salary_aud: null, contract_day_rate_aud: null },
    );
  });

  it("compares list fields by value after a conflict", () => {
    const a = toDraft(SETTINGS);
    expect(changedFields(a, toDraft(SETTINGS))).toEqual([]);
    expect(changedFields(a, { ...a, work_modes: ["remote"] })).toEqual(["work_modes"]);
  });
});
