"use client";

import { usePathname } from "next/navigation";
import { createContext, type ReactNode, useContext, useEffect, useMemo, useRef } from "react";
import { onCLS, onFCP, onINP, onLCP, onTTFB } from "web-vitals";
import {
  type Analytics,
  type AnalyticsOptions,
  createAnalytics,
  type ProductEventName,
} from "./analytics";

interface AnalyticsContextValue {
  track(name: ProductEventName): void;
}

const noop: AnalyticsContextValue = { track: () => {} };
const AnalyticsContext = createContext<AnalyticsContextValue>(noop);

function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export interface AnalyticsProviderProps {
  send: AnalyticsOptions["send"];
  children: ReactNode;
}

/** Records page views (route templates), product events and Core Web Vitals for signed-in pages. */
export function AnalyticsProvider({ send, children }: AnalyticsProviderProps) {
  const pathname = usePathname() ?? "/";
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const sendRef = useRef(send);
  sendRef.current = send;

  const analytics = useMemo<Analytics>(
    () =>
      createAnalytics({
        send: (events, opts) => sendRef.current(events, opts),
        storage: localStorageOrNull(),
      }),
    [],
  );

  useEffect(() => {
    analytics.pageView(pathname);
  }, [analytics, pathname]);

  useEffect(() => {
    const report = (metric: { name: string; value: number; rating: string }) => {
      if (!["LCP", "INP", "CLS", "FCP", "TTFB"].includes(metric.name)) return;
      analytics.vital(
        {
          name: metric.name as "LCP",
          value: metric.value,
          rating: metric.rating as "good",
        },
        pathRef.current,
      );
    };
    onLCP(report);
    onINP(report);
    onCLS(report);
    onFCP(report);
    onTTFB(report);

    const onHide = () => {
      if (document.visibilityState === "hidden") void analytics.flush({ keepalive: true });
    };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      void analytics.flush({ keepalive: true });
    };
  }, [analytics]);

  const value = useMemo<AnalyticsContextValue>(
    () => ({ track: (name) => analytics.track(name, pathRef.current) }),
    [analytics],
  );
  return <AnalyticsContext.Provider value={value}>{children}</AnalyticsContext.Provider>;
}

/** `track("card_moved")` from any feature. A no-op outside the provider. */
export function useAnalytics(): AnalyticsContextValue {
  return useContext(AnalyticsContext);
}
