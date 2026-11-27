"use client";

import { useCallback, useEffect, useState } from "react";
import { Toast } from "@/components/Toast";
import { isApiError } from "@/lib/api";
import {
  SCAN_DOT_TEXT,
  SCAN_POLL_MS,
  scanDot,
  scanLine,
  scanResultText,
  useRunScan,
  useScanStatus,
} from "@/lib/scan";
import styles from "./Scan.module.css";

export interface ScanStatusBarProps {
  /** Scan status and Scan now are maintainer only (the API answers 403 otherwise). */
  maintainer: boolean;
}

/** Re-renders every `ms` so relative times ("3m ago") stay current between polls. */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(timer);
  }, [ms]);
  return now;
}

function failureText(error: unknown): string {
  if (isApiError(error) && error.status === 409) return "A scan is already running.";
  const ref = isApiError(error) && error.correlationId ? ` Reference: ${error.correlationId}` : "";
  return `Couldn't run the scan.${ref}`;
}

/**
 * The top bar's scan line, as on job-seeker: "● Last scan 3m ago · next in 12 min", refreshed
 * every 30 s, and Scan now (busy while it runs, then a toast with what it found).
 */
export function ScanStatusBar({ maintainer }: ScanStatusBarProps) {
  const q = useScanStatus(maintainer);
  const runScan = useRunScan();
  const now = useNow(SCAN_POLL_MS);
  const [toast, setToast] = useState("");
  const [failure, setFailure] = useState("");
  const dismiss = useCallback(() => setToast(""), []);

  if (!maintainer) return null;

  const status = q.data;
  const dot = scanDot(status);
  const running = runScan.isPending || Boolean(status?.running);
  const line = status ? scanLine(status, now) : q.isError ? "Scan status unavailable" : "";

  const onScan = () => {
    setToast("");
    setFailure("");
    runScan.mutate(undefined, {
      onSuccess: (run) => setToast(scanResultText(run)),
      onError: (error) => setFailure(failureText(error)),
    });
  };

  return (
    <div className={styles.bar}>
      <span className={styles.line} title={SCAN_DOT_TEXT[dot]}>
        <span className={`${styles.dot} ${styles[dot]}`} aria-hidden="true" />
        <span className="visually-hidden">{SCAN_DOT_TEXT[dot]}</span>
        {running ? <span className={styles.spinner} aria-hidden="true" /> : null}
        <span>{line}</span>
      </span>
      <button
        type="button"
        className={styles.button}
        disabled={running}
        aria-busy={runScan.isPending || undefined}
        onClick={onScan}
      >
        {running ? "Scanning…" : "Scan now"}
      </button>
      {failure ? (
        <span role="alert" className={styles.failure}>
          {failure}
        </span>
      ) : null}
      <Toast message={toast} onDismiss={dismiss} />
    </div>
  );
}
