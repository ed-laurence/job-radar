"use client";

import { useSyncExternalStore } from "react";

/** Mirrors the 1180px breakpoint in Dashboard.module.css (job-seeker's WIDE). */
export const WIDE_QUERY = "(min-width: 1180px)";

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const mq = window.matchMedia(WIDE_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function snapshot(): boolean {
  return (
    typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(WIDE_QUERY).matches
  );
}

/** True when the campaign column sits beside the main column. False on the server and in tests. */
export function useWide(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}
