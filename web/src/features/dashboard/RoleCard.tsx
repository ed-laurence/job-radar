import Link from "next/link";
import { labelFor, type Schemas } from "@/lib/api";
import { CardEvidence } from "./CardEvidence";
import {
  ACTION_LABELS,
  appliedAfter,
  type CardAction,
  cardActions,
  clock,
  countdown,
  scoreBand,
} from "./card";
import styles from "./Dashboard.module.css";
import { isActNow } from "./tabs";

type Role = Schemas["Role"];

const TRACK: Readonly<Record<Schemas["Track"], string>> = {
  contract: "Contract",
  exec: "Exec",
  mission: "Mission",
};
const WORK_MODE: Readonly<Record<Schemas["WorkMode"], string>> = {
  remote: "Remote",
  hybrid: "Hybrid",
  on_site: "On site",
};
const SOURCE: Readonly<Record<Schemas["RoleSource"], string>> = {
  feed: "Feed",
  manual: "Manual",
  link: "Link",
  seed: "Seed",
};

const BAND_CLASS = {
  high: styles.scoreHigh,
  mid: styles.scoreMid,
  low: styles.scoreLow,
  none: styles.scoreNone,
};
const HEAT_CLASS = { hot: styles.hot, warm: styles.warm, cold: "" };
interface Chip {
  text: string;
  className?: string;
}

export interface RoleCardProps {
  role: Role;
  now: number;
  busy: boolean;
  /** Added by a refresh since the last load: slides in with a brief highlight. */
  arrived?: boolean;
  onAction: (role: Role, action: CardAction) => void;
}

/** One role, laid out like job-seeker's card: score, title, chips, clock, why, evidence, actions. */
export function RoleCard({ role, now, busy, arrived = false, onAction }: RoleCardProps) {
  const titleId = `role-${role.id}-title`;
  const act = isActNow(role.status);
  const band = scoreBand(role.score);
  const time = clock(role, now);
  const left = act ? countdown(role, now) : undefined;
  const after = act ? undefined : appliedAfter(role);
  const hasKit = Boolean(role.kit?.cover_note || role.kit?.answers?.length || role.kit?.cv);
  const chips: Chip[] = [
    role.track ? { text: TRACK[role.track] } : undefined,
    role.mission ? { text: role.mission, className: styles.chipMission } : undefined,
    role.work_mode ? { text: WORK_MODE[role.work_mode] } : undefined,
    role.pay ? { text: role.pay } : undefined,
    { text: SOURCE[role.source] },
    act ? undefined : { text: labelFor("RoleStatus", role.status), className: styles.chipStatus },
  ].filter((c): c is Chip => Boolean(c?.text));
  return (
    <article
      className={[
        styles.card,
        time.heat === "hot" && act ? styles.fresh : "",
        arrived ? styles.arrived : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-labelledby={titleId}
    >
      <div className={styles.cardTop}>
        <div className={`${styles.score} ${BAND_CLASS[band]}`}>
          <span className="visually-hidden">Score </span>
          {role.score ?? "–"}
        </div>
        <div className={styles.cardTitle}>
          <h3 id={titleId}>
            <Link href={`/roles/${role.id}`}>{role.title}</Link>
          </h3>
          <p className={styles.company}>
            {[role.company, role.location].filter(Boolean).join(" · ") || "Company not known"}
          </p>
          {chips.length ? (
            <ul className={styles.chips} aria-label="Details">
              {chips.map((c) => (
                <li key={c.text} className={`${styles.chip} ${c.className ?? ""}`}>
                  {c.text}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
      {time.text ? (
        <p className={`${styles.clock} ${HEAT_CLASS[time.heat]}`}>
          <time dateTime={role.posted_at ?? role.found_at}>{time.text}</time>
          {left ? (
            <>
              <span className={styles.track} aria-hidden="true">
                <span className={styles.fill} style={{ width: `${left.pct}%` }} />
              </span>
              <span>{left.left}</span>
            </>
          ) : after ? (
            <span>· {after}</span>
          ) : null}
        </p>
      ) : null}
      {role.why ? <p className={styles.why}>{role.why}</p> : null}
      {act ? <CardEvidence role={role} /> : null}
      <div className={styles.cardActions}>
        {role.url ? (
          <a className={styles.primary} href={role.url} target="_blank" rel="noopener noreferrer">
            Open listing ↗<span className="visually-hidden"> (opens in a new tab)</span>
          </a>
        ) : null}
        {hasKit ? null : (
          <Link className={styles.button} href={`/roles/${role.id}#kit`}>
            <span aria-hidden="true">✎ </span>Write kit
          </Link>
        )}
        {cardActions(role.status).map((a) => (
          <button
            key={a}
            type="button"
            className={styles.button}
            disabled={busy}
            aria-describedby={titleId}
            onClick={() => onAction(role, a)}
          >
            {ACTION_LABELS[a]}
          </button>
        ))}
        <Link className={styles.button} href={`/roles/${role.id}#chat`}>
          <span aria-hidden="true">💬 </span>Ask
        </Link>
      </div>
      <details className={styles.disclosure}>
        <summary>{hasKit ? "Application kit" : "Notes · write a kit"}</summary>
        <div className={styles.disclosureBody}>
          {role.notes ? <p>{role.notes}</p> : <p className={styles.sub}>No notes yet.</p>}
          <p>
            <Link href={`/roles/${role.id}#kit`}>
              {hasKit ? "Open the kit and notes" : "Write a kit and edit notes"}
            </Link>
          </p>
        </div>
      </details>
    </article>
  );
}
