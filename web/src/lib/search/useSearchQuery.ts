"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

export const SEARCH_DEBOUNCE_MS = 250;

/** Writes `?q=` (or removes it) without a navigation; Next keeps useSearchParams in step. */
function writeUrl(q: string) {
  const params = new URLSearchParams(window.location.search);
  if (q) params.set("q", q);
  else params.delete("q");
  const search = params.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`,
  );
}

/**
 * A list search kept in the URL: `text` follows the field at once, `query` (trimmed) follows it
 * after a short pause and is what the list fetches. Back/forward or a link that changes `?q=`
 * resets both.
 */
export function useSearchQuery() {
  const urlQ = useSearchParams()?.get("q") ?? "";
  const [text, setText] = useState(urlQ);
  const [query, setQuery] = useState(urlQ.trim());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const written = useRef(urlQ);

  useEffect(() => {
    if (urlQ === written.current) return;
    written.current = urlQ;
    clearTimeout(timer.current);
    setText(urlQ);
    setQuery(urlQ.trim());
  }, [urlQ]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const commit = useCallback((value: string) => {
    const q = value.trim();
    written.current = q;
    setQuery(q);
    writeUrl(q);
  }, []);

  const change = useCallback(
    (value: string) => {
      setText(value);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => commit(value), SEARCH_DEBOUNCE_MS);
    },
    [commit],
  );

  const clear = useCallback(() => {
    clearTimeout(timer.current);
    setText("");
    commit("");
  }, [commit]);

  return { text, query, change, clear };
}
