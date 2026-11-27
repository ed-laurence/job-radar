import type { Schemas } from "@/lib/api";
import styles from "./DevBoard.module.css";
import type { Task } from "./model";
import { ROUTE_LABEL } from "./triageLabels";

export interface TaskBadgesProps {
  task: Task;
}

/** Badge colours, each a text/soft-background token pair that meets WCAG AA in both themes. */
type Tone = "danger" | "hot" | "warm" | "cold" | "accent" | "mission" | "neutral";

const TONE_CLASS: Record<Tone, string | undefined> = {
  danger: styles.badgeDanger,
  hot: styles.badgeHot,
  warm: styles.badgeWarm,
  cold: styles.badgeCold,
  accent: styles.badgeAccent,
  mission: styles.badgeMission,
  neutral: styles.badgeNeutral,
};

/** Urgency runs red → orange → amber → grey for both priority and route. */
const PRIORITY_TONE: Record<Schemas["TaskPriority"], Tone> = {
  P0: "danger",
  P1: "hot",
  P2: "warm",
  P3: "cold",
};

const ROUTE_TONE: Record<Schemas["TriageRoute"], Tone> = {
  now: "hot",
  next: "warm",
  later: "cold",
};

const RISK_TONE: Record<NonNullable<Task["risk"]>, Tone> = {
  high: "danger",
  medium: "warm",
  low: "accent",
};

const TYPE_TONE: Record<NonNullable<Task["type"]>, Tone> = {
  security: "danger",
  bug: "hot",
  feature: "accent",
  performance: "mission",
  chore: "cold",
  refactor: "cold",
  test: "cold",
  docs: "cold",
};

/** Labels shown on the card; the rest are counted (all of them are on the detail page). */
const MAX_LABELS = 3;

function badgeClass(tone: Tone): string {
  return [styles.badge, TONE_CLASS[tone]].filter(Boolean).join(" ");
}

/** The card's detail badges. Every badge is text; colour only reinforces it. */
export function TaskBadges({ task }: TaskBadgesProps) {
  const locked = task.column === "out_of_scope";
  const labels = task.labels ?? [];
  const hiddenLabels = labels.slice(MAX_LABELS);
  return (
    <ul className={styles.badges} aria-label="Details">
      {locked ? <li className={badgeClass("danger")}>Locked</li> : null}
      {task.priority ? (
        <li className={badgeClass(PRIORITY_TONE[task.priority])}>
          <span className="visually-hidden">Priority </span>
          {task.priority}
        </li>
      ) : null}
      {task.column === "triaged" && task.route ? (
        <li className={badgeClass(ROUTE_TONE[task.route])}>{ROUTE_LABEL[task.route]}</li>
      ) : null}
      {task.type ? <li className={badgeClass(TYPE_TONE[task.type])}>{task.type}</li> : null}
      {task.size ? <li className={badgeClass("neutral")}>Size {task.size}</li> : null}
      {task.risk ? <li className={badgeClass(RISK_TONE[task.risk])}>Risk {task.risk}</li> : null}
      {(task.areas ?? []).map((area) => (
        <li key={`area-${area}`} className={badgeClass("mission")}>
          <span className="visually-hidden">Area </span>
          {area}
        </li>
      ))}
      {labels.slice(0, MAX_LABELS).map((label) => (
        <li key={`label-${label}`} className={badgeClass("neutral")}>
          <span className="visually-hidden">Label </span>
          {label}
        </li>
      ))}
      {hiddenLabels.length ? (
        <li className={badgeClass("neutral")} title={hiddenLabels.join(", ")}>
          +{hiddenLabels.length}
          <span className="visually-hidden">
            {" "}
            more {hiddenLabels.length === 1 ? "label" : "labels"}
          </span>
        </li>
      ) : null}
      {task.active_run_id ? <li className={badgeClass("accent")}>Agent running</li> : null}
    </ul>
  );
}
