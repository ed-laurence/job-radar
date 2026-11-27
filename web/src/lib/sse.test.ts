import { describe, expect, it, vi } from "vitest";
import {
  applyGenerationEvent,
  applyStepEvent,
  type GenerationEvent,
  initialGenerationState,
  initialStepState,
  openEventStream,
  REDACTED,
  SseParser,
  type StreamEvent,
  toStreamEvent,
} from "./sse";

const RUN = "00000000-0000-4000-8000-0000000000f1";

function frame(event: string, id: string, data: unknown): string {
  return `event: ${event}\nid: ${id}\ndata: ${JSON.stringify(data)}\n\n`;
}

function step(seq: number) {
  return {
    id: `00000000-0000-4000-8000-0000000000${String(seq).padStart(2, "0")}`,
    run_id: RUN,
    seq,
    kind: "message",
    summary: `step ${seq}`,
    at: "2026-11-25T01:00:00Z",
  };
}

/** A Response whose body yields the given chunks, then either ends or errors. */
function sseResponse(chunks: string[], opts: { breakAfter?: boolean; status?: number } = {}) {
  const encoder = new TextEncoder();
  let next = 0;
  // Pull-based so every chunk is read before the stream ends or breaks.
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[next++];
      if (chunk !== undefined) controller.enqueue(encoder.encode(chunk));
      else if (opts.breakAfter) controller.error(new TypeError("network dropped"));
      else controller.close();
    },
  });
  return new Response(body, {
    status: opts.status ?? 200,
    headers: { "content-type": "text/event-stream" },
  });
}

describe("SseParser", () => {
  it("assembles messages split across chunks and CRLF line endings", () => {
    const p = new SseParser();
    expect(p.push("event: segm")).toEqual([]);
    expect(p.push('ent\r\nid: 1\r\ndata: {"a":')).toEqual([]);
    expect(p.push("1}\r\n\r\n")).toEqual([{ event: "segment", id: "1", data: '{"a":1}' }]);
  });

  it("joins multi-line data, ignores comments and keeps the last id", () => {
    const p = new SseParser();
    const out = p.push(": keep-alive\n\nid: 7\ndata: a\ndata: b\n\ndata: c\n\n");
    expect(out).toEqual([
      { event: "message", id: "7", data: "a\nb" },
      { event: "message", id: "7", data: "c" },
    ]);
  });

  it("reads the retry field", () => {
    const p = new SseParser();
    p.push("retry: 2500\n\n");
    expect(p.retryMs).toBe(2500);
  });
});

describe("toStreamEvent", () => {
  it("types known events", () => {
    const e = toStreamEvent({
      event: "segment",
      id: "s1",
      data: JSON.stringify({ segment_id: "a", seq: 0, text: "Hi" }),
    });
    expect(e).toEqual({
      event: "segment",
      id: "s1",
      data: { segment_id: "a", seq: 0, text: "Hi" },
    });
  });

  it.each([
    { event: "unknown", id: "1", data: "{}" },
    { event: "segment", id: "1", data: "not json" },
    { event: "segment", id: "1", data: JSON.stringify({ seq: 0 }) },
    { event: "step", id: "1", data: JSON.stringify({ summary: "x" }) },
  ])("drops malformed or unknown events: $event $data", (raw) => {
    expect(toStreamEvent(raw)).toBeNull();
  });
});

describe("applyGenerationEvent", () => {
  const seg = (id: string, seq: number, text: string): GenerationEvent => ({
    event: "segment",
    id: `e-${id}`,
    data: { segment_id: id, seq, text },
  });

  it("orders segments by seq", () => {
    let s = initialGenerationState();
    s = applyGenerationEvent(s, seg("b", 1, "World"));
    s = applyGenerationEvent(s, seg("a", 0, "Hello"));
    expect(s.segments.map((x) => x.text)).toEqual(["Hello", "World"]);
  });

  it("replaces a retracted segment with [redacted]", () => {
    let s = applyGenerationEvent(initialGenerationState(), seg("a", 0, "secret value"));
    s = applyGenerationEvent(s, {
      event: "retract",
      id: "r1",
      data: { segment_id: "a", replacement: "[redacted]", reason: "secret" },
    });
    expect(s.segments[0]).toMatchObject({ text: REDACTED, redacted: true });
    expect(JSON.stringify(s)).not.toContain("secret value");
  });

  it("redacts a segment whose retraction arrived first", () => {
    let s = applyGenerationEvent(initialGenerationState(), {
      event: "retract",
      id: "r1",
      data: { segment_id: "a", replacement: "[redacted]" },
    });
    s = applyGenerationEvent(s, seg("a", 0, "leak"));
    expect(s.segments[0]?.text).toBe(REDACTED);
  });

  it("records done and error as terminal", () => {
    const done = applyGenerationEvent(initialGenerationState(), {
      event: "done",
      id: "d",
      data: { result_id: RUN },
    });
    expect(done).toMatchObject({ status: "done", resultId: RUN });
    const failed = applyGenerationEvent(initialGenerationState(), {
      event: "error",
      id: "x",
      data: { type: "/problems/internal", title: "Internal", status: 500, correlation_id: "c" },
    });
    expect(failed.status).toBe("error");
    expect(failed.error?.correlation_id).toBe("c");
  });
});

describe("applyStepEvent", () => {
  it("dedupes replayed steps and keeps seq order", () => {
    let s = initialStepState();
    for (const n of [0, 2, 1, 2]) {
      s = applyStepEvent(s, { event: "step", id: String(n), data: step(n) } as StreamEvent);
    }
    expect(s.steps.map((x) => x.seq)).toEqual([0, 1, 2]);
  });
});

describe("openEventStream", () => {
  const noSleep = (_ms: number) => Promise.resolve();

  it("sends the bearer token and emits typed events until done", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        sseResponse([frame("step", "0", step(0)), frame("done", "d", { status: "succeeded" })]),
      );
    const events: StreamEvent[] = [];
    const outcome = await openEventStream({
      baseUrl: "http://api.test/api",
      path: `/runs/${RUN}/steps`,
      getToken: async () => "fake:owner@example.test",
      onEvent: (e) => events.push(e),
      fetch: fetchMock,
      sleep: noSleep,
    });
    expect(outcome.kind).toBe("done");
    expect(events.map((e) => e.event)).toEqual(["step", "done"]);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe(`http://api.test/api/runs/${RUN}/steps`);
    const headers = new Headers(init?.headers);
    expect(headers.get("authorization")).toBe("Bearer fake:owner@example.test");
    expect(headers.get("accept")).toBe("text/event-stream");
  });

  it("reconnects a resumable stream with Last-Event-ID after a drop", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(sseResponse([frame("step", "4", step(4))], { breakAfter: true }))
      .mockResolvedValueOnce(sseResponse([frame("step", "5", step(5)), frame("done", "d", {})]));
    const sleep = vi.fn(noSleep);
    const events: StreamEvent[] = [];
    const outcome = await openEventStream({
      baseUrl: "http://api.test/api",
      path: `/runs/${RUN}/steps`,
      getToken: async () => "t",
      onEvent: (e) => events.push(e),
      fetch: fetchMock,
      sleep,
    });
    expect(outcome.kind).toBe("done");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("last-event-id")).toBe("4");
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(events.filter((e) => e.event === "step")).toHaveLength(2);
  });

  it("backs off exponentially and gives up after maxRetries", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline"));
    const sleep = vi.fn(noSleep);
    const events: StreamEvent[] = [];
    const outcome = await openEventStream({
      baseUrl: "http://api.test/api",
      path: `/runs/${RUN}/steps`,
      getToken: async () => "t",
      onEvent: (e) => events.push(e),
      fetch: fetchMock,
      sleep,
      maxRetries: 3,
      baseDelayMs: 100,
      random: () => 1,
    });
    expect(outcome.kind).toBe("failed");
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([100, 200, 400]);
    expect(events.at(-1)?.event).toBe("error");
  });

  it("never re-sends a non-resumable POST stream", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      sseResponse([frame("segment", "1", { segment_id: "a", seq: 0, text: "Hi" })], {
        breakAfter: true,
      }),
    );
    const events: StreamEvent[] = [];
    const outcome = await openEventStream({
      baseUrl: "http://api.test/api",
      path: "/roles/00000000-0000-4000-8000-0000000000a1/kit",
      method: "POST",
      body: { parts: ["cv"] },
      getToken: async () => "t",
      onEvent: (e) => events.push(e),
      fetch: fetchMock,
      sleep: noSleep,
    });
    expect(outcome.kind).toBe("failed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(events.map((e) => e.event)).toEqual(["segment", "error"]);
  });

  it("does not retry a 4xx and surfaces the problem", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "/problems/forbidden",
          title: "Forbidden",
          status: 403,
          correlation_id: "c403",
        }),
        { status: 403, headers: { "content-type": "application/problem+json" } },
      ),
    );
    const outcome = await openEventStream({
      baseUrl: "http://api.test/api",
      path: `/runs/${RUN}/steps`,
      getToken: async () => "t",
      onEvent: () => {},
      fetch: fetchMock,
      sleep: noSleep,
    });
    expect(outcome).toMatchObject({
      kind: "error",
      problem: { status: 403, correlation_id: "c403" },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("stops quietly when aborted", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    });
    const outcome = await openEventStream({
      baseUrl: "http://api.test/api",
      path: `/runs/${RUN}/steps`,
      getToken: async () => "t",
      onEvent: () => {},
      fetch: fetchMock,
      sleep: noSleep,
      signal: controller.signal,
    });
    expect(outcome.kind).toBe("aborted");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
