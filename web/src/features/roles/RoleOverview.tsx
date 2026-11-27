import { Markdown } from "@/components/Markdown";
import { formatDate, type Role, STATUS_LABELS, TRACK_LABELS, WORK_MODE_LABELS } from "./labels";
import styles from "./Roles.module.css";

/** Ad links come from feeds and pastes: only plain web links become clickable. */
const SAFE_LINK = /^https?:\/\//i;

export interface RoleOverviewProps {
  role: Role;
}

/** Read-only view. Ad text and notes are user data (plain text); `why` is model output (sanitized markdown). */
export function RoleOverview({ role }: RoleOverviewProps) {
  return (
    <>
      <div className={styles.header}>
        <h1 id="page-title">{role.title}</h1>
        <span className={styles.muted}>
          {STATUS_LABELS[role.status]}
          {role.score !== undefined ? ` · score ${role.score}` : ""}
        </span>
      </div>
      <section className={styles.panel} aria-label="Role details">
        <dl className={styles.meta}>
          <dt>Company</dt>
          <dd>{role.company ?? "—"}</dd>
          <dt>Location</dt>
          <dd>{role.location ?? "—"}</dd>
          <dt>Work mode</dt>
          <dd>{role.work_mode ? WORK_MODE_LABELS[role.work_mode] : "—"}</dd>
          <dt>Pay</dt>
          <dd>{role.pay ?? "—"}</dd>
          <dt>Track</dt>
          <dd>{role.track ? TRACK_LABELS[role.track] : "—"}</dd>
          <dt>Posted</dt>
          <dd>{formatDate(role.posted_at)}</dd>
          <dt>Applied</dt>
          <dd>{formatDate(role.applied_at)}</dd>
          <dt>Ad</dt>
          <dd>
            {role.url && SAFE_LINK.test(role.url) ? (
              <a href={role.url} target="_blank" rel="noopener noreferrer nofollow">
                Open the original ad
              </a>
            ) : (
              "—"
            )}
          </dd>
        </dl>
        {role.notes ? (
          <>
            <h2>Notes</h2>
            <p className={styles.adText}>{role.notes}</p>
          </>
        ) : null}
      </section>
      {role.why || role.risks?.length ? (
        <section className={styles.panel} aria-labelledby="why-title">
          <h2 id="why-title">Why it scored {role.score ?? "—"}</h2>
          {role.why ? <Markdown>{role.why}</Markdown> : null}
          {role.risks?.length ? (
            <ul aria-label="Risks">
              {role.risks.map((risk) => (
                <li key={risk}>{risk}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
      {role.description ? (
        <section className={styles.panel} aria-labelledby="ad-title">
          <h2 id="ad-title">Ad text</h2>
          <p className={styles.adText}>{role.description}</p>
        </section>
      ) : null}
    </>
  );
}
