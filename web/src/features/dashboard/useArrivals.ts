"use client";

import { useEffect, useRef, useState } from "react";

const NONE: ReadonlySet<string> = new Set();

export interface Arrivals {
  /** Ids that appeared at the top of the list since the last load: they get the slide-in. */
  ids: ReadonlySet<string>;
  /** A polite announcement for screen readers, e.g. "2 new roles arrived.". */
  note: string;
}

/**
 * Tracks which roles a refresh (a poll after a scheduled scan) added, as job-seeker does. The
 * first load of a list (`key`: the tab and filters) seeds what's known without highlighting;
 * after that, ids new on the first page are arrivals. Later pages ("Show more") are only
 * remembered, never highlighted.
 */
export function useArrivals(
  key: string,
  firstPage: readonly string[] | undefined,
  all: readonly string[],
): Arrivals {
  const seen = useRef<{ key: string; ids: Set<string> } | null>(null);
  const [arrivals, setArrivals] = useState<Arrivals>({ ids: NONE, note: "" });
  const firstKey = firstPage?.join(",");
  const allKey = all.join(",");

  useEffect(() => {
    if (firstKey === undefined) return;
    const first = firstKey ? firstKey.split(",") : [];
    const every = allKey ? allKey.split(",") : [];
    const prev = seen.current;
    if (!prev || prev.key !== key) {
      seen.current = { key, ids: new Set(every) };
      setArrivals({ ids: NONE, note: "" });
      return;
    }
    const fresh = first.filter((id) => !prev.ids.has(id));
    for (const id of every) prev.ids.add(id);
    if (fresh.length) {
      setArrivals({
        ids: new Set(fresh),
        note: `${fresh.length} new role${fresh.length === 1 ? "" : "s"} arrived.`,
      });
    }
  }, [key, firstKey, allKey]);

  return arrivals;
}
