import type { Schemas } from "./api";

export type WebEvent = Schemas["WebEvent"];
export type ProductEventName = Schemas["ProductEvent"]["name"];
export type VitalMetric = Schemas["WebVitalEvent"]["metric"];
export type VitalRating = NonNullable<Schemas["WebVitalEvent"]["rating"]>;

/** Same pattern the API enforces on `route`. */
export const ROUTE_TEMPLATE_PATTERN = /^\/$|^(\/([a-z]+(-[a-z]+)*|\[[a-z][a-zA-Z]{0,30}\]))+$/;

const WORD = /^[a-z]+(-[a-z]+)*$/;
const PARAM_AFTER: Record<string, string> = {
  roles: "roleId",
  contacts: "contactId",
  targets: "targetId",
  "research-runs": "researchRunId",
  tasks: "taskId",
  runs: "runId",
};
const SESSION_KEY = "jr.analytics.session";
const MAX_QUEUE = 50;
const MAX_VITAL = 600_000;

/**
 * Turns a concrete pathname into its route template: any segment that isn't a plain lower-case
 * word (an id, a number, an email) becomes a `[param]`, so ids never leave the browser.
 */
export function toRouteTemplate(pathname: string): string | null {
  const path = pathname.split(/[?#]/, 1)[0] ?? "/";
  const segments = path.split("/").filter(Boolean);
  if (segments.length === 0) return "/";
  const template = `/${segments
    .map((seg, i) => {
      if (WORD.test(seg)) return seg;
      const previous = i > 0 ? segments[i - 1] : undefined;
      return `[${(previous && PARAM_AFTER[previous]) || "id"}]`;
    })
    .join("/")}`;
  return ROUTE_TEMPLATE_PATTERN.test(template) ? template : null;
}

function randomId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

let memorySession: { day: string; id: string } | undefined;

/** A random id rotated every UTC day. Not tied to the user; stored locally, never in a cookie. */
export function getSessionId(storage: Storage | null, now: Date): string {
  const day = now.toISOString().slice(0, 10);
  let stored: { day: string; id: string } | undefined;
  try {
    const raw = storage?.getItem(SESSION_KEY);
    stored = raw ? (JSON.parse(raw) as { day: string; id: string }) : memorySession;
  } catch {
    stored = memorySession;
  }
  if (stored?.day === day && /^[A-Za-z0-9_-]{16,32}$/.test(stored.id)) return stored.id;
  const fresh = { day, id: randomId() };
  memorySession = fresh;
  try {
    storage?.setItem(SESSION_KEY, JSON.stringify(fresh));
  } catch {
    // Storage can be full or blocked; the in-memory id still works for this page.
  }
  return fresh.id;
}

export interface VitalSample {
  name: VitalMetric;
  value: number;
  rating?: VitalRating;
}

export interface AnalyticsOptions {
  send: (events: WebEvent[], options: { keepalive: boolean }) => Promise<void>;
  now?: () => Date;
  storage?: Storage | null;
  maxBatch?: number;
  /** 0 disables the timer (flush on size or page hide only). */
  flushIntervalMs?: number;
}

export interface Analytics {
  pageView(pathname: string): void;
  track(name: ProductEventName, pathname: string): void;
  vital(sample: VitalSample, pathname: string): void;
  flush(options?: { keepalive?: boolean }): Promise<void>;
  pending(): number;
  dispose(): void;
}

export function createAnalytics(options: AnalyticsOptions): Analytics {
  const now = options.now ?? (() => new Date());
  const storage = options.storage === undefined ? null : options.storage;
  const maxBatch = Math.min(options.maxBatch ?? 20, MAX_QUEUE);
  const interval = options.flushIntervalMs ?? 5000;
  let queue: WebEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;

  const base = (pathname: string) => {
    const route = toRouteTemplate(pathname);
    if (!route) return null;
    const at = now();
    return { route, at: at.toISOString(), session_id: getSessionId(storage, at) };
  };

  const flush = async ({ keepalive = false }: { keepalive?: boolean } = {}) => {
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
    if (queue.length === 0) return;
    const batch = queue;
    queue = [];
    try {
      await options.send(batch, { keepalive });
    } catch {
      // Analytics is best effort: a failed batch is dropped rather than retried.
    }
  };

  const enqueue = (event: WebEvent | null) => {
    if (!event || queue.length >= MAX_QUEUE) return;
    queue.push(event);
    if (queue.length >= maxBatch) {
      void flush();
    } else if (interval > 0 && timer === undefined) {
      timer = setTimeout(() => void flush(), interval);
    }
  };

  return {
    pageView(pathname) {
      const b = base(pathname);
      enqueue(b && { type: "page_view", ...b });
    },
    track(name, pathname) {
      const b = base(pathname);
      enqueue(b && { type: "product", name, ...b });
    },
    vital(sample, pathname) {
      const b = base(pathname);
      const value = Math.min(MAX_VITAL, Math.max(0, sample.value));
      enqueue(
        b && {
          type: "web_vital",
          metric: sample.name,
          value,
          ...(sample.rating ? { rating: sample.rating } : {}),
          ...b,
        },
      );
    },
    flush,
    pending: () => queue.length,
    dispose() {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      queue = [];
    },
  };
}
