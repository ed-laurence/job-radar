"use client";

import { type FormEvent, type ReactNode, useCallback, useId, useState } from "react";
import { Toast } from "@/components/Toast";
import { describeError, isVersionConflict } from "@/lib/api";
import { useFetchLatestSettings, useSaveSettings } from "./api";
import { ChipInput } from "./ChipInput";
import {
  COMMON_ZONES,
  changedFields,
  type Draft,
  type DraftField,
  type Errors,
  FIELD_ORDER,
  INT_RULES,
  type IntField,
  LOCATION_MAX,
  MAX_LOCATIONS,
  PAY_RULES,
  type PayField,
  type Settings,
  SPELLINGS,
  type Spelling,
  TEXT_MAX,
  toDraft,
  toUpdate,
  validate,
  WEIGHT_FIELDS,
  WORK_MODES,
  type WorkMode,
  weightTotal,
} from "./model";
import styles from "./Settings.module.css";

const OTHER_ZONE = "__other";

const LABELS: Record<DraftField, string> = {
  skills: "Skills and experience not in your CVs",
  brief: "What you're looking for",
  keep_score: "Keep score",
  borderline_score: "Borderline score",
  notify_score: "Notify score",
  notify_mission_score: "Mission notify score",
  weight_skills: "Skills",
  weight_track_pay: "Track and pay",
  weight_location: "Location",
  weight_freshness: "Freshness",
  perm_salary_aud: "Permanent salary floor (AUD a year)",
  contract_day_rate_aud: "Contract day rate floor (AUD a day)",
  work_modes: "Accepted work modes",
  preferred_locations: "Preferred locations",
  after_hours: "Suggest a follow-up after (hours)",
  due_hours: "Follow-up due after (hours)",
  nudge_days: "Remind again after (days)",
  auto_kits: "Write kits automatically",
  kits_per_run: "Kits per run",
  spelling: "Spelling",
  start_date: "Campaign start date",
  archive_default_days: "Archive older than (days)",
  timezone: "Time zone",
  monthly_budget_aud: "Monthly LLM budget (AUD)",
};

const isWeight = (f: DraftField) => (WEIGHT_FIELDS as readonly DraftField[]).includes(f);

/** The first field to focus: form order, with the weights total on the first weight. */
function firstInvalid(found: Errors): DraftField | undefined {
  return FIELD_ORDER.find((f) => found[f] || (f === "weight_skills" && found.weights));
}

export interface SettingsFormProps {
  initial: Settings;
  maintainer: boolean;
}

interface Conflict {
  changed: DraftField[];
}

function Group({
  id,
  title,
  hint,
  children,
}: {
  id?: string;
  title: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <fieldset id={id} className={styles.group} tabIndex={id ? -1 : undefined}>
      <legend className={styles.legend}>{title}</legend>
      {hint ? <p className={styles.muted}>{hint}</p> : null}
      <div className={styles.fields}>{children}</div>
    </fieldset>
  );
}

/**
 * Every user setting except job scraping. Saves with the version that was read; on a 409 it
 * loads the latest version and keeps the user's edits in the form so they can be re-applied.
 */
export function SettingsForm({ initial, maintainer }: SettingsFormProps) {
  const id = useId();
  const save = useSaveSettings();
  const fetchLatest = useFetchLatestSettings();
  const [base, setBase] = useState<Settings>(initial);
  const [draft, setDraft] = useState<Draft>(() => toDraft(initial));
  const [errors, setErrors] = useState<Errors>({});
  const [otherZone, setOtherZone] = useState(
    () => !COMMON_ZONES.includes(initial.display.timezone),
  );
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [toast, setToast] = useState("");
  const dismissToast = useCallback(() => setToast(""), []);

  const fid = (f: DraftField) => `${id}-${f}`;
  const set = <K extends DraftField>(field: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [field]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = validate(draft, { maintainer });
    setErrors(found);
    const first = firstInvalid(found);
    if (first) {
      focusField(first);
      return;
    }
    setToast("");
    save.mutate(toUpdate(draft, base, { maintainer }), {
      onSuccess: (saved) => {
        setBase(saved);
        setDraft(toDraft(saved));
        setConflict(null);
        setToast("Settings saved.");
      },
      onError: async (error) => {
        if (!isVersionConflict(error)) return;
        try {
          const latest = await fetchLatest();
          const before = toDraft(base);
          const after = toDraft(latest);
          setBase(latest);
          setConflict({ changed: changedFields(before, after) });
        } catch {
          setConflict({ changed: [] });
        }
      },
    });
  };

  const focusField = (f: DraftField) => {
    // Work modes is a group of checkboxes; its first box takes focus.
    const target = f === "work_modes" ? `${fid(f)}-${WORK_MODES[0]?.value}` : fid(f);
    document.getElementById(target)?.focus();
  };

  const discard = () => {
    setDraft(toDraft(base));
    setOtherZone(!COMMON_ZONES.includes(base.display.timezone));
    setErrors({});
    setConflict(null);
    save.reset();
  };

  const described = (f: DraftField, extra?: string) =>
    [errors[f] ? `${fid(f)}-error` : "", extra ?? ""].filter(Boolean).join(" ") || undefined;

  const total = weightTotal(draft);
  const weightsHint = `${id}-weights-total`;
  const weightsError = `${id}-weights-error`;

  const errorText = (f: DraftField) =>
    errors[f] ? (
      <p id={`${fid(f)}-error`} className={styles.error}>
        {errors[f]}
      </p>
    ) : null;

  const numberField = (f: IntField, hint?: string) => {
    const rule = INT_RULES[f];
    return (
      <div className={styles.field} key={f}>
        <label htmlFor={fid(f)}>{LABELS[f]}</label>
        <input
          id={fid(f)}
          className={styles.input}
          type="number"
          inputMode="numeric"
          min={rule.min}
          max={rule.max}
          step={1}
          value={draft[f]}
          aria-invalid={errors[f] || (isWeight(f) && errors.weights) ? true : undefined}
          aria-describedby={described(
            f,
            [
              hint ? `${fid(f)}-hint` : "",
              isWeight(f) ? weightsHint : "",
              isWeight(f) && errors.weights ? weightsError : "",
            ]
              .filter(Boolean)
              .join(" ") || undefined,
          )}
          onChange={(e) => set(f, e.target.value)}
        />
        {hint ? (
          <p id={`${fid(f)}-hint`} className={styles.muted}>
            {hint}
          </p>
        ) : null}
        {errorText(f)}
      </div>
    );
  };

  const payField = (f: PayField, hint: string) => (
    <div className={styles.field} key={f}>
      <label htmlFor={fid(f)}>{LABELS[f]}</label>
      <input
        id={fid(f)}
        className={styles.input}
        type="number"
        inputMode="numeric"
        min={0}
        max={PAY_RULES[f].max}
        step={1}
        value={draft[f]}
        aria-invalid={errors[f] ? true : undefined}
        aria-describedby={described(f, `${fid(f)}-hint`)}
        onChange={(e) => set(f, e.target.value)}
      />
      <p id={`${fid(f)}-hint`} className={styles.muted}>
        {hint}
      </p>
      {errorText(f)}
    </div>
  );

  const toggleMode = (mode: WorkMode, on: boolean) =>
    setDraft((d) => ({
      ...d,
      work_modes: on ? [...d.work_modes, mode] : d.work_modes.filter((m) => m !== mode),
    }));

  const textArea = (f: "skills" | "brief", hint: string) => (
    <div className={`${styles.field} ${styles.wide}`}>
      <label htmlFor={fid(f)}>{LABELS[f]}</label>
      <p id={`${fid(f)}-hint`} className={styles.muted}>
        {hint}
      </p>
      <textarea
        id={fid(f)}
        className={styles.textarea}
        maxLength={TEXT_MAX}
        value={draft[f]}
        aria-invalid={errors[f] ? true : undefined}
        aria-describedby={described(f, `${fid(f)}-hint ${fid(f)}-count`)}
        onChange={(e) => set(f, e.target.value)}
      />
      <p id={`${fid(f)}-count`} className={styles.counter}>
        {draft[f].length} / {TEXT_MAX} characters
      </p>
      {errorText(f)}
    </div>
  );

  const otherError =
    save.isError && !isVersionConflict(save.error) ? describeError(save.error) : null;

  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-label="Settings">
      <Group
        id="skills"
        title="Your extra skills"
        hint="Treated as fact alongside your CVs: scoring and kits use it exactly like CV text, so keep it accurate."
      >
        {textArea(
          "skills",
          "Skills and experience that aren't in your CVs. One per line works well: a skill, where you used it and when.",
        )}
        {textArea("brief", "Roles, pay, location and deal-breakers.")}
      </Group>

      <section
        id="scoring"
        className={styles.formSection}
        aria-labelledby="scoring-title"
        tabIndex={-1}
      >
        <h2 id="scoring-title" className={styles.sectionTitle}>
          Scoring
        </h2>
        <Group
          title="Scoring thresholds"
          hint="Scores are 0 to 100. Keep must be at least borderline."
        >
          {numberField("keep_score", "Roles at or above this stay in Act now.")}
          {numberField("borderline_score", "Roles at or above this are borderline.")}
          {numberField("notify_score", "Notify me about roles at or above this.")}
          {numberField("notify_mission_score", "The notify threshold for mission-driven roles.")}
        </Group>

        <Group
          title="Scoring weights"
          hint="Points each part of a role's score can earn. They must total 100."
        >
          {numberField("weight_skills", "How well your CVs and skills match the ad.")}
          {numberField(
            "weight_track_pay",
            "Permanent or contract, and the pay against your floors.",
          )}
          {numberField("weight_location", "Work mode and place against what you accept.")}
          {numberField("weight_freshness", "How recently the role was posted.")}
          <div className={`${styles.field} ${styles.wide}`}>
            <p
              id={weightsHint}
              className={total === 100 ? styles.ok : styles.warn}
              aria-live="polite"
            >
              {total === 100 ? `✓ Total ${total} of 100` : `Total ${total}: must total 100`}
            </p>
            {errors.weights ? (
              <p id={weightsError} className={styles.error}>
                {errors.weights}
              </p>
            ) : null}
          </div>
        </Group>

        <Group
          title="Pay floors"
          hint="Roles paying below a floor score lower. Blank means no floor."
        >
          {payField("perm_salary_aud", "Base salary, excluding super.")}
          {payField("contract_day_rate_aud", "Excluding GST.")}
        </Group>

        <Group title="Work modes and locations">
          <fieldset
            className={`${styles.inner} ${styles.wide}`}
            aria-describedby={`${fid("work_modes")}-hint`}
          >
            <legend className={styles.innerLegend}>{LABELS.work_modes}</legend>
            <p id={`${fid("work_modes")}-hint`} className={styles.muted}>
              None ticked accepts any.
            </p>
            <div className={styles.checks}>
              {WORK_MODES.map((m) => (
                <label
                  key={m.value}
                  className={styles.check}
                  htmlFor={`${fid("work_modes")}-${m.value}`}
                >
                  <input
                    id={`${fid("work_modes")}-${m.value}`}
                    type="checkbox"
                    checked={draft.work_modes.includes(m.value)}
                    onChange={(e) => toggleMode(m.value, e.target.checked)}
                  />
                  {m.label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className={`${styles.field} ${styles.wide}`}>
            <label htmlFor={fid("preferred_locations")}>{LABELS.preferred_locations}</label>
            <ChipInput
              id={fid("preferred_locations")}
              label={LABELS.preferred_locations}
              values={draft.preferred_locations}
              max={MAX_LOCATIONS}
              maxLength={LOCATION_MAX}
              invalid={Boolean(errors.preferred_locations)}
              describedBy={described("preferred_locations", `${fid("preferred_locations")}-hint`)}
              onChange={(next) => set("preferred_locations", next)}
            />
            <p id={`${fid("preferred_locations")}-hint`} className={styles.muted}>
              Up to {MAX_LOCATIONS} cities or regions. Press Enter after each one.
            </p>
            {errorText("preferred_locations")}
          </div>
        </Group>
      </section>

      <section
        id="general"
        className={styles.formSection}
        aria-labelledby="general-title"
        tabIndex={-1}
      >
        <h2 id="general-title" className={styles.sectionTitle}>
          Follow-ups, kits and display
        </h2>

        <Group title="Follow-ups">
          {numberField("after_hours")}
          {numberField("due_hours")}
          {numberField("nudge_days")}
        </Group>

        <Group title="Kits">
          <div className={`${styles.field} ${styles.wide}`}>
            <label className={styles.check} htmlFor={fid("auto_kits")}>
              <input
                id={fid("auto_kits")}
                type="checkbox"
                checked={draft.auto_kits}
                onChange={(e) => set("auto_kits", e.target.checked)}
              />
              {LABELS.auto_kits} for new high-scoring roles
            </label>
          </div>
          {numberField("kits_per_run")}
          <div className={styles.field}>
            <label htmlFor={fid("spelling")}>{LABELS.spelling}</label>
            <select
              id={fid("spelling")}
              className={styles.select}
              value={draft.spelling}
              onChange={(e) => set("spelling", e.target.value as Spelling)}
            >
              {SPELLINGS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </Group>

        <Group title="Campaign and archive">
          <div className={styles.field}>
            <label htmlFor={fid("start_date")}>{LABELS.start_date}</label>
            <input
              id={fid("start_date")}
              className={styles.input}
              type="date"
              value={draft.start_date}
              aria-invalid={errors.start_date ? true : undefined}
              aria-describedby={described("start_date", `${fid("start_date")}-hint`)}
              onChange={(e) => set("start_date", e.target.value)}
            />
            <p id={`${fid("start_date")}-hint`} className={styles.muted}>
              Week 1 of the plan. Blank uses the plan's own start.
            </p>
            {errorText("start_date")}
          </div>
          {numberField("archive_default_days", "The Dashboard's Archive control starts here.")}
        </Group>

        <Group title="Display">
          <div className={styles.field}>
            <label htmlFor={otherZone ? `${fid("timezone")}-select` : fid("timezone")}>
              {LABELS.timezone}
            </label>
            <select
              id={otherZone ? `${fid("timezone")}-select` : fid("timezone")}
              className={styles.select}
              value={otherZone ? OTHER_ZONE : draft.timezone}
              aria-invalid={!otherZone && errors.timezone ? true : undefined}
              aria-describedby={otherZone ? undefined : described("timezone")}
              onChange={(e) => {
                if (e.target.value === OTHER_ZONE) {
                  setOtherZone(true);
                } else {
                  setOtherZone(false);
                  set("timezone", e.target.value);
                }
              }}
            >
              {COMMON_ZONES.map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, " ")}
                </option>
              ))}
              <option value={OTHER_ZONE}>Other…</option>
            </select>
            {otherZone ? (
              <>
                <label htmlFor={fid("timezone")}>Time zone name</label>
                <input
                  id={fid("timezone")}
                  className={styles.input}
                  maxLength={64}
                  placeholder="Region/City"
                  value={draft.timezone}
                  aria-invalid={errors.timezone ? true : undefined}
                  aria-describedby={described("timezone")}
                  onChange={(e) => set("timezone", e.target.value)}
                />
              </>
            ) : null}
            {errorText("timezone")}
          </div>
        </Group>

        {maintainer ? (
          <Group title="LLM budget" hint="Maintainers only.">
            <div className={styles.field}>
              <label htmlFor={fid("monthly_budget_aud")}>{LABELS.monthly_budget_aud}</label>
              <input
                id={fid("monthly_budget_aud")}
                className={styles.input}
                type="number"
                inputMode="decimal"
                min={0}
                max={10_000}
                step="0.01"
                value={draft.monthly_budget_aud}
                aria-invalid={errors.monthly_budget_aud ? true : undefined}
                aria-describedby={described(
                  "monthly_budget_aud",
                  `${fid("monthly_budget_aud")}-hint`,
                )}
                onChange={(e) => set("monthly_budget_aud", e.target.value)}
              />
              <p id={`${fid("monthly_budget_aud")}-hint`} className={styles.muted}>
                Blank means no cap beyond the per-feature limits.
              </p>
              {errorText("monthly_budget_aud")}
            </div>
          </Group>
        ) : null}
      </section>

      {conflict ? (
        <div role="alert" className={styles.alert}>
          <h2>These settings changed elsewhere</h2>
          <p>
            The latest version is loaded. Your edits are still in the form: check them and save
            again to apply them on top.
          </p>
          {conflict.changed.length ? (
            <ul aria-label="Changed elsewhere">
              {conflict.changed.map((f) => (
                <li key={f}>{LABELS[f]}</li>
              ))}
            </ul>
          ) : null}
          <div className={styles.actions}>
            <button type="button" className={styles.button} onClick={discard}>
              Discard my edits
            </button>
          </div>
        </div>
      ) : null}
      {otherError ? (
        <div role="alert" className={styles.alert}>
          <h2>{otherError.title}</h2>
          {[otherError.description, ...otherError.fieldErrors].filter(Boolean).map((m) => (
            <p key={m}>{m}</p>
          ))}
          {otherError.correlationId ? <p>Reference: {otherError.correlationId}</p> : null}
        </div>
      ) : null}

      <div className={`${styles.actions} ${styles.saveBar}`}>
        <button type="submit" className={styles.primary} disabled={save.isPending}>
          {save.isPending ? "Saving…" : conflict ? "Save my edits again" : "Save settings"}
        </button>
      </div>
      <Toast message={toast} onDismiss={dismissToast} />
    </form>
  );
}
