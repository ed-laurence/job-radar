"use client";

import { type FormEvent, useId, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { Markdown } from "@/components/Markdown";
import { enumValues, isApiError, isNotFound, labelFor, type Schemas } from "@/lib/api";
import {
  useCampaignActivity,
  useCampaignPlan,
  useCampaignTasks,
  useLogActivity,
  useToggleTask,
} from "./api";
import styles from "./Dashboard.module.css";

type Plan = Schemas["CampaignPlan"];
type Kind = Schemas["CampaignActivityKind"];

const DAY_MONTH = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

/** "27 Nov" from an ISO date. */
export function dayMonth(date: string): string {
  const t = Date.parse(date);
  return Number.isNaN(t) ? date : DAY_MONTH.format(t);
}

function localToday(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const reference = (e: unknown) =>
  isApiError(e) && e.correlationId ? ` Reference: ${e.correlationId}` : "";

const OUTCOMES = ["screens", "interviews", "offers"];

function PlanPanel({ plan }: { plan: Plan }) {
  const inPlan = plan.current_week >= 1 && plan.current_week <= plan.total_weeks;
  const outcomes = plan.metrics
    .filter((m) => OUTCOMES.includes(m.key))
    .map((m) => `${m.actual} ${m.label.toLowerCase()}`)
    .join(" · ");
  const bars = plan.metrics.filter((m) => m.target > 0);
  return (
    <section className={styles.panel} aria-labelledby="plan-title">
      <div className={styles.planHead}>
        <h2 id="plan-title" className={styles.caps}>
          {plan.name}
        </h2>
        <span className={styles.sub}>
          {dayMonth(plan.start)} – {dayMonth(plan.end)}
        </span>
      </div>
      {inPlan || plan.week !== plan.current_week ? (
        <p>
          <b>
            Week {plan.week} of {plan.total_weeks}
          </b>{" "}
          <span className={styles.sub}>
            {dayMonth(plan.week_start)} – {dayMonth(plan.week_end)}
            {outcomes ? ` · ${outcomes}` : ""}
          </span>
        </p>
      ) : (
        <p className={styles.sub}>
          {plan.current_week < 1
            ? "The campaign hasn't started yet."
            : "The campaign has finished."}
        </p>
      )}
      {bars.length ? (
        <dl className={styles.bars} aria-label="This week against target">
          {bars.map((m) => (
            <div key={m.key} className={styles.barrow}>
              <dt>{m.label}</dt>
              <dd>
                <span className={styles.barTrack} aria-hidden="true">
                  <span
                    className={styles.barFill}
                    style={{ width: `${Math.min(100, (m.actual / m.target) * 100)}%` }}
                  />
                </span>
                <span className={styles.barValue}>
                  {m.actual}/{m.target}
                  <span className="visually-hidden"> done</span>
                </span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {plan.checkpoint ? (
        <p className={styles.checkpoint}>
          <b>
            Checkpoint, end of week {plan.checkpoint.week} ({dayMonth(plan.checkpoint.due)}):
          </b>{" "}
          {plan.checkpoint.text}
        </p>
      ) : null}
    </section>
  );
}

interface TasksPanelProps {
  plan: Plan;
  onWeek: (week: number) => void;
}

function TasksPanel({ plan, onWeek }: TasksPanelProps) {
  const tasks = useCampaignTasks(plan.week);
  const toggle = useToggleTask();
  const [alert, setAlert] = useState("");
  const items = tasks.data?.items ?? [];
  const done = items.filter((t) => t.done).length;
  const inPlan = plan.current_week >= 1 && plan.current_week <= plan.total_weeks;

  return (
    <section className={styles.panel} aria-labelledby="tasks-title">
      <div className={styles.panelHead}>
        <h2 id="tasks-title" className={styles.caps}>
          Tasks · week {plan.week}{" "}
          <span className={styles.sub}>
            {dayMonth(plan.week_start)} – {dayMonth(plan.week_end)}
          </span>
        </h2>
        <span className={styles.btns}>
          <button
            type="button"
            className={`${styles.button} ${styles.small}`}
            aria-label="Previous week"
            disabled={plan.week <= 1}
            onClick={() => onWeek(plan.week - 1)}
          >
            ‹
          </button>
          {inPlan && plan.week !== plan.current_week ? (
            <button
              type="button"
              className={`${styles.button} ${styles.small}`}
              onClick={() => onWeek(plan.current_week)}
            >
              This week
            </button>
          ) : null}
          <button
            type="button"
            className={`${styles.button} ${styles.small}`}
            aria-label="Next week"
            disabled={plan.week >= plan.total_weeks}
            onClick={() => onWeek(plan.week + 1)}
          >
            ›
          </button>
        </span>
      </div>
      {alert ? (
        <p role="alert" className={styles.alert}>
          {alert}
        </p>
      ) : null}
      {tasks.isPending ? (
        <LoadingState label="Loading tasks…" />
      ) : tasks.isError ? (
        <ErrorState
          title="Tasks failed to load"
          correlationId={isApiError(tasks.error) ? tasks.error.correlationId : undefined}
          onRetry={() => void tasks.refetch()}
        />
      ) : items.length === 0 ? (
        <p className={styles.sub}>No tasks planned for this week.</p>
      ) : (
        <>
          <p className={styles.sub}>
            {done} of {items.length} done. Open a task for the steps and links.
          </p>
          <ul className={styles.tasks}>
            {items.map((t) => (
              <li key={t.id} className={t.done ? `${styles.task} ${styles.taskDone}` : styles.task}>
                <input
                  type="checkbox"
                  checked={t.done}
                  aria-label={`Done: ${t.title}`}
                  disabled={toggle.isPending && toggle.variables?.taskId === t.id}
                  onChange={(e) => {
                    setAlert("");
                    toggle.mutate(
                      { taskId: t.id, week: plan.week, done: e.target.checked },
                      {
                        onError: (err) =>
                          setAlert(`Couldn't update “${t.title}”.${reference(err)}`),
                      },
                    );
                  }}
                />
                <details>
                  <summary>
                    <span className={styles.taskTitle}>{t.title}</span>
                    {t.when ? <span className={styles.sub}>{t.when}</span> : null}
                    {t.progress ? (
                      <span
                        className={
                          t.progress.actual >= t.progress.target
                            ? `${styles.chip} ${styles.chipStatus}`
                            : styles.chip
                        }
                      >
                        <span className={styles.mono}>
                          {t.progress.actual}/{t.progress.target}
                        </span>
                      </span>
                    ) : null}
                  </summary>
                  <div className={styles.taskBody}>
                    {t.steps ? <Markdown>{t.steps}</Markdown> : null}
                    {t.links.length ? (
                      <div className={styles.cardActions}>
                        {t.links.map((l) => (
                          <a
                            key={l.url}
                            className={`${styles.button} ${styles.small}`}
                            href={l.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {l.label} ↗
                            <span className="visually-hidden"> (opens in a new tab)</span>
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

const KINDS = enumValues.CampaignActivityKind as readonly Kind[];

function LogActivity({ week }: { week: number }) {
  const id = useId();
  const log = useLogActivity();
  const activity = useCampaignActivity(week);
  const [kind, setKind] = useState<Kind>(KINDS[0] ?? "note");
  const [date, setDate] = useState(localToday);
  const [who, setWho] = useState("");
  const [company, setCompany] = useState("");
  const [notes, setNotes] = useState("");
  const [notice, setNotice] = useState("");
  const [alert, setAlert] = useState("");

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setNotice("");
    setAlert("");
    const body: Schemas["CampaignActivityCreate"] = {
      kind,
      on_date: date,
      ...(who.trim() ? { who: who.trim() } : {}),
      ...(company.trim() ? { company: company.trim() } : {}),
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    };
    log.mutate(body, {
      onSuccess: () => {
        setNotice(`Logged: ${labelFor("CampaignActivityKind", kind)}.`);
        setWho("");
        setCompany("");
        setNotes("");
      },
      onError: (err) => setAlert(`Couldn't log that.${reference(err)}`),
    });
  };

  const items = activity.data?.items ?? [];
  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className={styles.caps}>
        Log activity
      </h2>
      <form className={styles.logform} onSubmit={onSubmit}>
        <div className={styles.grid}>
          <label>
            What
            <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {labelFor("CampaignActivityKind", k)}
                </option>
              ))}
            </select>
          </label>
          <label>
            When
            <input type="date" required value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label>
            Who
            <input
              type="text"
              placeholder="Person or agency"
              value={who}
              onChange={(e) => setWho(e.target.value)}
            />
          </label>
          <label>
            Company
            <input
              type="text"
              placeholder="Organisation"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </label>
        </div>
        <label>
          Notes
          <input
            type="text"
            placeholder="Role, next step, what they said…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </label>
        <div className={styles.formActions}>
          <button type="submit" className={styles.primary} disabled={log.isPending}>
            {log.isPending ? "Logging…" : "Log it"}
          </button>
          <span className={styles.sub}>Applications and follow-ups count automatically.</span>
        </div>
        <p role="status" className={styles.notice}>
          {notice}
        </p>
        {alert ? (
          <p role="alert" className={styles.alert}>
            {alert}
          </p>
        ) : null}
      </form>
      {items.length ? (
        <>
          <h3 className={styles.caps}>Logged this week</h3>
          <ul className={styles.rows}>
            {items.map((a) => (
              <li key={a.id}>
                <span className={`${styles.chip} ${styles.chipStatus}`}>
                  {labelFor("CampaignActivityKind", a.kind)}
                </span>
                <span>{[a.who, a.company].filter(Boolean).join(", ") || "–"}</span>
                <span className={styles.sub}>
                  {dayMonth(a.on_date)}
                  {a.notes ? ` · ${a.notes}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

/** The campaign plan: the week's targets, its tasks, and the activity log. */
export function CampaignColumn() {
  const [week, setWeek] = useState<number | undefined>(undefined);
  const plan = useCampaignPlan(week);
  if (plan.isPending) return <LoadingState label="Loading the campaign plan…" />;
  if (plan.isError) {
    if (isNotFound(plan.error)) {
      return (
        <div className={styles.empty}>
          <b>No campaign plan yet</b>
          <p>Once a plan is set up, its weekly targets, tasks and checkpoints show here.</p>
        </div>
      );
    }
    return (
      <ErrorState
        title="The campaign plan failed to load"
        correlationId={isApiError(plan.error) ? plan.error.correlationId : undefined}
        onRetry={() => void plan.refetch()}
      />
    );
  }
  return (
    <div className={styles.campaign}>
      <PlanPanel plan={plan.data} />
      <TasksPanel plan={plan.data} onWeek={setWeek} />
      <LogActivity week={plan.data.week} />
    </div>
  );
}
