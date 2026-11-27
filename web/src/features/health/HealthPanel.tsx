"use client";

import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import { poolText, useScanStatus } from "@/lib/scan";
import { useHealthSummary } from "./api";
import styles from "./Health.module.css";
import {
  budgetText,
  formatVital,
  type HealthSummary,
  JOB_STATUS_TEXT,
  jobName,
  money,
  rate,
  vitalStatus,
  vitalsSummary,
  when,
  whole,
} from "./health";
import { SpendByFeatureChart } from "./SpendByFeatureChart";
import { SpendByProviderChart } from "./SpendByProviderChart";

const VITAL_STATUS_TEXT = { over: "Over budget", within: "Within budget", none: "No budget" };

function Jobs({ jobs, pool }: { jobs: HealthSummary["jobs"]; pool: number | undefined }) {
  return (
    <section className={styles.section} aria-labelledby="health-jobs">
      <h2 id="health-jobs" className={styles.heading}>
        Scheduled jobs
      </h2>
      {jobs.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">Scheduled jobs</caption>
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Last run</th>
                <th scope="col">Status</th>
                <th scope="col">Next run</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.name}>
                  <th scope="row">{jobName(j.name)}</th>
                  <td>{when(j.last_run_at)}</td>
                  <td className={j.last_status === "ok" ? styles.ok : styles.bad}>
                    {JOB_STATUS_TEXT[j.last_status]}
                  </td>
                  <td>{when(j.next_run_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.muted}>No scheduled jobs reported.</p>
      )}
      {pool === undefined ? null : <p className={styles.muted}>{poolText(pool)}</p>}
    </section>
  );
}

function Llm({ llm }: { llm: HealthSummary["llm"] }) {
  const stats: [string, string][] = [
    ["Spend today (AUD)", money(llm.spend_today_aud)],
    ["Spend this month (AUD)", budgetText(llm.spend_month_aud, llm.budget_month_aud)],
    ["Calls today", whole(llm.calls_today)],
    ["Fallback rate, 7 days", rate(llm.fallback_rate_7d)],
  ];
  return (
    <section className={styles.section} aria-labelledby="health-llm">
      <h2 id="health-llm" className={styles.heading}>
        LLM usage
      </h2>
      <dl className={styles.meta}>
        {stats.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className={styles.charts}>
        <SpendByFeatureChart />
        <SpendByProviderChart />
      </div>
    </section>
  );
}

function Site({ site }: { site: HealthSummary["site"] }) {
  return (
    <section className={styles.section} aria-labelledby="health-site">
      <h2 id="health-site" className={styles.heading}>
        Site usage
      </h2>
      <dl className={styles.meta}>
        <div>
          <dt>Page views, 7 days</dt>
          <dd>{whole(site.page_views_7d)}</dd>
        </div>
        <div>
          <dt>Sessions, 7 days</dt>
          <dd>{whole(site.sessions_7d)}</dd>
        </div>
      </dl>
      <p className={styles.muted}>{vitalsSummary(site.web_vitals_p75)}</p>
      {site.web_vitals_p75.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">Web Vitals p75 against budget</caption>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th scope="col">p75</th>
                <th scope="col">Budget</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {site.web_vitals_p75.map((v) => {
                const status = vitalStatus(v);
                return (
                  <tr key={v.metric}>
                    <th scope="row">{v.metric}</th>
                    <td>{formatVital(v.metric, v.value)}</td>
                    <td>
                      {v.budget === null || v.budget === undefined
                        ? "—"
                        : formatVital(v.metric, v.budget)}
                    </td>
                    <td
                      className={
                        status === "over" ? styles.bad : status === "within" ? styles.ok : ""
                      }
                    >
                      {VITAL_STATUS_TEXT[status]}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

export interface HealthPanelProps {
  maintainer: boolean;
}

/** The contents of the Health menu: jobs, LLM usage and site usage (maintainers only). */
export function HealthPanel({ maintainer }: HealthPanelProps) {
  const q = useHealthSummary(maintainer);
  const scan = useScanStatus(maintainer);
  if (!maintainer) {
    return <p className={styles.muted}>Health details are available to maintainers.</p>;
  }
  if (q.isPending) return <LoadingState label="Loading health…" />;
  if (q.isError) {
    return (
      <ErrorState
        title="Health failed to load"
        correlationId={isApiError(q.error) ? q.error.correlationId : undefined}
        onRetry={() => void q.refetch()}
      />
    );
  }
  return (
    <>
      <Jobs jobs={q.data.jobs} pool={scan.data?.pool_remaining} />
      <Llm llm={q.data.llm} />
      <Site site={q.data.site} />
      <p className={styles.muted}>As of {when(q.data.as_of)}</p>
    </>
  );
}
