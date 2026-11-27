"use client";

import { type CSSProperties, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import { ActionRow } from "./ActionRow";
import { AlertsBanner } from "./AlertsBanner";
import { type Track, useJobSummary } from "./api";
import { CampaignColumn } from "./CampaignColumn";
import { ColumnSplitter, useColumnSplit } from "./ColumnSplitter";
import styles from "./Dashboard.module.css";
import { FeedbackPanel } from "./FeedbackPanel";
import { JobAnalytics } from "./JobAnalytics";
import { JobSummaryTiles } from "./JobSummaryTiles";
import { OutreachPanel } from "./OutreachPanel";
import { RoleCards } from "./RoleCards";
import { StatusTabs } from "./StatusTabs";
import { isRoleTab, type View, visibleTabs } from "./tabs";
import { useWide } from "./useWide";

/**
 * The main page, as on job-seeker: on wide screens the campaign column sits beside the main
 * column (alerts, tiles, job analytics, status tabs, action row, role cards); narrower, it's the
 * Campaign tab.
 */
export function DashboardPage() {
  const wide = useWide();
  const summary = useJobSummary();
  const [chosen, setView] = useState<View>("act");
  const [track, setTrack] = useState<Track | undefined>(undefined);
  const [notice, setNotice] = useState("");
  const [alert, setAlert] = useState("");
  const [split, setSplit] = useColumnSplit();
  const [layout, setLayout] = useState<HTMLDivElement | null>(null);
  const counts = summary.data?.tab_counts;
  // The campaign column replaces the Campaign tab on wide screens.
  const view: View = wide && chosen === "campaign" ? "act" : chosen;

  return (
    <section className={styles.page} aria-labelledby="page-title">
      <h1 id="page-title" className="visually-hidden">
        Dashboard
      </h1>
      <div
        ref={setLayout}
        className={styles.layout}
        style={wide ? ({ "--campaign-split": split } as CSSProperties) : undefined}
      >
        {wide ? (
          <>
            <aside className={styles.campcol} aria-label="Campaign plan">
              <CampaignColumn />
            </aside>
            <ColumnSplitter value={split} onChange={setSplit} container={layout} />
          </>
        ) : null}
        <div className={styles.maincol}>
          <AlertsBanner />
          {summary.isPending ? (
            <LoadingState label="Loading headline numbers…" />
          ) : summary.isError ? (
            <ErrorState
              title="Headline numbers failed to load"
              correlationId={isApiError(summary.error) ? summary.error.correlationId : undefined}
              onRetry={() => void summary.refetch()}
            />
          ) : (
            <JobSummaryTiles summary={summary.data} />
          )}
          <JobAnalytics />
          <div className={styles.bar}>
            <StatusTabs
              tabs={visibleTabs(counts, wide)}
              current={view}
              counts={counts}
              followupsDue={summary.data?.followups_due}
              onChange={setView}
            />
            <ActionRow
              track={track}
              onTrack={setTrack}
              showArchive={view === "act"}
              onNotice={setNotice}
              onAlert={setAlert}
            />
          </div>
          {isRoleTab(view) ? (
            <RoleCards
              tab={view}
              track={track}
              notice={notice}
              alert={alert}
              onNotice={setNotice}
              onAlert={setAlert}
            />
          ) : view === "outreach" ? (
            <OutreachPanel />
          ) : view === "feedback" ? (
            <FeedbackPanel />
          ) : (
            <CampaignColumn />
          )}
        </div>
      </div>
    </section>
  );
}
