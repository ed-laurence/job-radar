"use client";

import type { MouseEvent } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError } from "@/lib/api";
import { useRole } from "@/lib/session";
import { formatDateTime, useUserSettings } from "@/lib/settings";
import { ConnectionsSection } from "./ConnectionsSection";
import { DocumentsSection } from "./DocumentsSection";
import { ExperienceSection } from "./ExperienceSection";
import styles from "./Settings.module.css";
import { SettingsForm } from "./SettingsForm";

/** The page's sections in page order; each `id` is an anchor on the page. */
export const SECTIONS: readonly { id: string; label: string }[] = [
  { id: "cvs", label: "CVs & LinkedIn profile" },
  { id: "skills", label: "Your extra skills" },
  { id: "scoring", label: "Scoring" },
  { id: "general", label: "Follow-ups, kits and display" },
  { id: "experience", label: "Experience not on your CV" },
  { id: "connections", label: "LinkedIn connections" },
];

/** Moves focus with the scroll so keyboard and screen reader users land in the section. */
function jump(e: MouseEvent<HTMLAnchorElement>, id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  e.preventDefault();
  target.scrollIntoView?.({ block: "start" });
  target.focus({ preventScroll: true });
  window.history.replaceState(null, "", `#${id}`);
}

/**
 * The Settings tab: CVs, extra skills, scoring, follow-ups, kits, display and budget, experience
 * not on your CV, and LinkedIn connections.
 */
export function SettingsPage() {
  const settings = useUserSettings();
  const role = useRole();
  const data = settings.data;
  const timeZone = data?.display.timezone ?? "UTC";
  return (
    <section className={styles.page} aria-labelledby="settings-title">
      <div className={styles.head}>
        <h1 id="settings-title">Settings</h1>
        {data?.updated_at ? (
          <p className={styles.muted}>
            Last saved {formatDateTime(data.updated_at, data.display.timezone)}
          </p>
        ) : null}
      </div>
      {settings.isPending ? (
        <LoadingState label="Loading settings…" />
      ) : settings.isError ? (
        <ErrorState
          title="Settings failed to load"
          correlationId={isApiError(settings.error) ? settings.error.correlationId : undefined}
          onRetry={() => void settings.refetch()}
        />
      ) : role === undefined ? (
        <LoadingState label="Loading settings…" />
      ) : (
        <>
          <nav className={styles.nav} aria-label="Settings sections">
            <ul>
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <a href={`#${s.id}`} onClick={(e) => jump(e, s.id)}>
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <DocumentsSection timeZone={timeZone} />
          <SettingsForm initial={settings.data} maintainer={role === "maintainer"} />
          <ExperienceSection />
          <ConnectionsSection timeZone={timeZone} />
        </>
      )}
    </section>
  );
}
