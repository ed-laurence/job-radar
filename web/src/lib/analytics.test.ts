import { describe, expect, it, vi } from "vitest";
import {
  createAnalytics,
  getSessionId,
  ROUTE_TEMPLATE_PATTERN,
  toRouteTemplate,
  type WebEvent,
} from "./analytics";

const NOW = new Date("2026-11-25T01:00:00Z");

function setup(overrides: Partial<Parameters<typeof createAnalytics>[0]> = {}) {
  const batches: { events: WebEvent[]; keepalive: boolean }[] = [];
  const send = vi.fn(async (events: WebEvent[], opts: { keepalive: boolean }) => {
    batches.push({ events, keepalive: opts.keepalive });
  });
  const analytics = createAnalytics({
    send,
    now: () => NOW,
    storage: window.localStorage,
    flushIntervalMs: 0,
    ...overrides,
  });
  return { analytics, batches, send };
}

describe("toRouteTemplate", () => {
  it.each([
    ["/", "/"],
    ["/dashboard", "/dashboard"],
    ["/roles/0b6f3a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "/roles/[roleId]"],
    ["/dev-board/tasks/42?tab=plan#x", "/dev-board/tasks/[taskId]"],
    ["/runs/abc123/steps/", "/runs/[runId]/steps"],
    ["/something/Owner%40example.test", "/something/[id]"],
  ])("%s -> %s", (path, template) => {
    expect(toRouteTemplate(path)).toBe(template);
    expect(ROUTE_TEMPLATE_PATTERN.test(template)).toBe(true);
  });
});

describe("getSessionId", () => {
  it("is random, pattern-valid and stable within a UTC day", () => {
    const a = getSessionId(window.localStorage, NOW);
    expect(a).toMatch(/^[A-Za-z0-9_-]{16,32}$/);
    expect(getSessionId(window.localStorage, new Date("2026-11-25T23:59:00Z"))).toBe(a);
  });

  it("rotates daily", () => {
    const a = getSessionId(window.localStorage, NOW);
    expect(getSessionId(window.localStorage, new Date("2026-11-26T00:00:01Z"))).not.toBe(a);
  });

  it("works without storage", () => {
    expect(getSessionId(null, NOW)).toMatch(/^[A-Za-z0-9_-]{16,32}$/);
  });
});

describe("createAnalytics", () => {
  it("records page views as route templates only, never ids", async () => {
    const { analytics, batches } = setup();
    analytics.pageView("/roles/0b6f3a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b");
    await analytics.flush();
    const [event] = batches[0]?.events ?? [];
    expect(event).toMatchObject({
      type: "page_view",
      route: "/roles/[roleId]",
      at: NOW.toISOString(),
    });
    expect(JSON.stringify(batches)).not.toContain("0b6f3a8e");
  });

  it("sends only schema fields: no emails, names or free text", async () => {
    const { analytics, batches } = setup();
    analytics.track("card_moved", "/dev-board");
    analytics.vital({ name: "LCP", value: 1830.4, rating: "good" }, "/dashboard");
    await analytics.flush();
    const [product, vital] = batches[0]?.events ?? [];
    expect(Object.keys(product ?? {}).sort()).toEqual([
      "at",
      "name",
      "route",
      "session_id",
      "type",
    ]);
    expect(Object.keys(vital ?? {}).sort()).toEqual([
      "at",
      "metric",
      "rating",
      "route",
      "session_id",
      "type",
      "value",
    ]);
    expect(vital).toMatchObject({ type: "web_vital", metric: "LCP", value: 1830.4 });
  });

  it("batches events and flushes when the batch is full", async () => {
    const { analytics, send } = setup({ maxBatch: 3 });
    analytics.pageView("/roles");
    analytics.pageView("/pipeline");
    expect(send).not.toHaveBeenCalled();
    analytics.pageView("/contacts");
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0]?.[0]).toHaveLength(3);
    expect(analytics.pending()).toBe(0);
  });

  it("flushes on a timer", async () => {
    vi.useFakeTimers();
    try {
      const { analytics, send } = setup({ flushIntervalMs: 5000 });
      analytics.pageView("/roles");
      expect(send).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(5000);
      expect(send).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses keepalive when flushing on page hide", async () => {
    const { analytics, batches } = setup();
    analytics.pageView("/roles");
    await analytics.flush({ keepalive: true });
    expect(batches[0]?.keepalive).toBe(true);
  });

  it("drops a failed batch instead of retrying in a loop", async () => {
    const send = vi.fn().mockRejectedValue(new Error("offline"));
    const analytics = createAnalytics({ send, now: () => NOW, storage: null, flushIntervalMs: 0 });
    analytics.pageView("/roles");
    await expect(analytics.flush()).resolves.toBeUndefined();
    expect(analytics.pending()).toBe(0);
    await analytics.flush();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("clamps vitals into the accepted range", async () => {
    const { analytics, batches } = setup();
    analytics.vital({ name: "INP", value: 9_999_999, rating: "poor" }, "/roles");
    analytics.vital({ name: "CLS", value: -1, rating: "good" }, "/roles");
    await analytics.flush();
    expect(batches[0]?.events.map((e) => (e.type === "web_vital" ? e.value : null))).toEqual([
      600000, 0,
    ]);
  });
});
