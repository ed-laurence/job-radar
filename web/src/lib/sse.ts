import { DEFAULT_API_BASE, isProblem, type Problem, type Schemas, type TokenSource } from "./api";

export type GenerationEvent = Schemas["GenerationStreamEvent"];
export type StepStreamEvent = Schemas["StepStreamEvent"];
export type StreamEvent = GenerationEvent | StepStreamEvent;
export type DoneEvent = Schemas["DoneEvent"];
export type Step = Schemas["Step"];

/** What a retracted segment shows. Always this constant, whatever the payload says (fail closed). */
export const REDACTED = "[redacted]";

// ---------------------------------------------------------------- wire parsing

export interface RawSseMessage {
  event: string;
  id: string | undefined;
  data: string;
}

/** Incremental `text/event-stream` parser (WHATWG HTML §9.2). Feed it decoded text chunks. */
export class SseParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];
  private hasData = false;
  lastEventId: string | undefined;
  retryMs: number | undefined;

  push(chunk: string): RawSseMessage[] {
    this.buffer += chunk;
    const out: RawSseMessage[] = [];
    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      if (!match) break;
      // A trailing CR may be the first half of CRLF; wait for the next chunk to decide.
      if (match[0] === "\r" && match.index === this.buffer.length - 1) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const message = this.line(line);
      if (message) out.push(message);
    }
    return out;
  }

  private line(line: string): RawSseMessage | null {
    if (line === "") {
      if (!this.hasData) {
        this.eventName = "";
        return null;
      }
      const message = {
        event: this.eventName || "message",
        id: this.lastEventId,
        data: this.dataLines.join("\n"),
      };
      this.eventName = "";
      this.dataLines = [];
      this.hasData = false;
      return message;
    }
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.eventName = value;
        break;
      case "data":
        this.dataLines.push(value);
        this.hasData = true;
        break;
      case "id":
        if (!value.includes("\0")) this.lastEventId = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.retryMs = Number(value);
        break;
    }
    return null;
  }
}

// ---------------------------------------------------------------- typing

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Types a raw message against the spec's event schemas. Unknown or malformed events are dropped. */
export function toStreamEvent(raw: RawSseMessage): StreamEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw.data);
  } catch {
    return null;
  }
  if (!isRecord(data)) return null;
  const id = raw.id ?? "";
  switch (raw.event) {
    case "segment":
      if (
        typeof data.segment_id !== "string" ||
        typeof data.seq !== "number" ||
        typeof data.text !== "string"
      ) {
        return null;
      }
      return { event: "segment", id, data: data as Schemas["SegmentEvent"]["data"] };
    case "retract":
      if (typeof data.segment_id !== "string") return null;
      return {
        event: "retract",
        id,
        data: { ...(data as Schemas["RetractEvent"]["data"]), replacement: REDACTED },
      };
    case "step":
      if (typeof data.seq !== "number" || typeof data.summary !== "string") return null;
      return { event: "step", id, data: data as Step };
    case "done":
      return { event: "done", id, data: data as DoneEvent["data"] };
    case "error":
      return {
        event: "error",
        id,
        data: isProblem(data) ? data : connectionProblem("Stream error"),
      };
    default:
      return null;
  }
}

function connectionProblem(title: string, status = 503): Problem {
  return { type: "about:blank", title, status, correlation_id: "" };
}

// ---------------------------------------------------------------- state reducers

export interface Segment {
  segmentId: string;
  seq: number;
  part: string | undefined;
  text: string;
  redacted: boolean;
}

export interface GenerationState {
  segments: Segment[];
  /** Retractions that arrived before their segment; that segment is redacted on arrival. */
  retracted: ReadonlySet<string>;
  status: "streaming" | "done" | "error";
  resultId: string | undefined;
  error: Problem | undefined;
}

export function initialGenerationState(): GenerationState {
  return {
    segments: [],
    retracted: new Set(),
    status: "streaming",
    resultId: undefined,
    error: undefined,
  };
}

export function applyGenerationEvent(state: GenerationState, event: StreamEvent): GenerationState {
  switch (event.event) {
    case "segment": {
      const { segment_id, seq, part, text } = event.data;
      const redacted = state.retracted.has(segment_id);
      const segment: Segment = {
        segmentId: segment_id,
        seq,
        part,
        text: redacted ? REDACTED : text,
        redacted,
      };
      const segments = state.segments.filter((s) => s.segmentId !== segment_id);
      segments.push(segment);
      segments.sort((a, b) => a.seq - b.seq);
      return { ...state, segments };
    }
    case "retract": {
      const id = event.data.segment_id;
      const retracted = new Set(state.retracted).add(id);
      const segments = state.segments.map((s) =>
        s.segmentId === id ? { ...s, text: REDACTED, redacted: true } : s,
      );
      return { ...state, segments, retracted };
    }
    case "done":
      return { ...state, status: "done", resultId: event.data.result_id };
    case "error":
      return { ...state, status: "error", error: event.data };
    default:
      return state;
  }
}

export interface StepState {
  steps: Step[];
  status: "streaming" | "done" | "error";
  error: Problem | undefined;
}

export function initialStepState(): StepState {
  return { steps: [], status: "streaming", error: undefined };
}

/** Steps are keyed by `seq`, so a replay after reconnecting never duplicates one. */
export function applyStepEvent(state: StepState, event: StreamEvent): StepState {
  switch (event.event) {
    case "step": {
      const steps = state.steps.filter((s) => s.seq !== event.data.seq);
      steps.push(event.data);
      steps.sort((a, b) => a.seq - b.seq);
      return { ...state, steps };
    }
    case "done":
      return { ...state, status: "done" };
    case "error":
      return { ...state, status: "error", error: event.data };
    default:
      return state;
  }
}

// ---------------------------------------------------------------- transport

export type StreamOutcome =
  | { kind: "done"; event: DoneEvent }
  | { kind: "error"; problem: Problem }
  | { kind: "failed"; problem: Problem }
  | { kind: "aborted" };

export interface EventStreamOptions {
  /** Path under the API base, e.g. `/runs/{id}/steps`. */
  path: string;
  method?: "GET" | "POST";
  body?: unknown;
  getToken: TokenSource;
  onEvent: (event: StreamEvent) => void;
  baseUrl?: string;
  signal?: AbortSignal;
  /**
   * Whether a dropped stream may be re-requested with `Last-Event-ID`. Defaults to true for GET
   * (step streams replay). A POST starts a new generation, so it is never re-sent.
   */
  resumable?: boolean;
  lastEventId?: string;
  maxRetries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  fetch?: typeof globalThis.fetch;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  random?: () => number;
}

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    }
    signal?.addEventListener("abort", done, { once: true });
  });
}

async function readProblem(response: Response): Promise<Problem> {
  try {
    const body: unknown = await response.json();
    if (isProblem(body)) return body;
  } catch {
    // Fall through to a generic problem: a non-JSON error body is never shown.
  }
  return {
    ...connectionProblem("Request failed", response.status),
    correlation_id: response.headers.get("X-Correlation-Id") ?? "",
  };
}

type ConnectionResult =
  | { kind: "terminal"; outcome: StreamOutcome }
  | { kind: "retry"; progressed: boolean; waitMs?: number };

/**
 * Opens an authenticated SSE stream (fetch-based, since EventSource can't send a bearer token or
 * a POST body). Resolves once the stream ends with `done`/`error`, fails, or is aborted.
 */
export async function openEventStream(options: EventStreamOptions): Promise<StreamOutcome> {
  const method = options.method ?? "GET";
  const resumable = options.resumable ?? method === "GET";
  const maxRetries = options.maxRetries ?? 5;
  const baseDelay = options.baseDelayMs ?? 1000;
  const maxDelay = options.maxDelayMs ?? 30_000;
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  const sleep = options.sleep ?? defaultSleep;
  const random = options.random ?? Math.random;
  const url = `${options.baseUrl ?? DEFAULT_API_BASE}${options.path}`;
  const parser = new SseParser();
  parser.lastEventId = options.lastEventId;

  const fail = (problem: Problem, kind: "error" | "failed"): StreamOutcome => {
    options.onEvent({ event: "error", id: "", data: problem });
    return { kind, problem };
  };

  const connect = async (): Promise<ConnectionResult> => {
    const headers = new Headers({ Accept: "text/event-stream" });
    const token = await options.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (resumable && parser.lastEventId) headers.set("Last-Event-ID", parser.lastEventId);

    const response = await doFetch(url, {
      method,
      headers,
      cache: "no-store",
      ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
      ...(options.signal ? { signal: options.signal } : {}),
    });

    if (!response.ok) {
      const retryAfter = Number(response.headers.get("Retry-After"));
      if (resumable && (response.status === 429 || response.status >= 500)) {
        return {
          kind: "retry",
          progressed: false,
          ...(retryAfter > 0 ? { waitMs: retryAfter * 1000 } : {}),
        };
      }
      return { kind: "terminal", outcome: fail(await readProblem(response), "error") };
    }
    if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
      return {
        kind: "terminal",
        outcome: fail(connectionProblem("Not an event stream", 502), "error"),
      };
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let progressed = false;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const raw of parser.push(decoder.decode(value, { stream: true }))) {
          const event = toStreamEvent(raw);
          if (!event) continue;
          progressed = true;
          options.onEvent(event);
          if (event.event === "done") {
            await reader.cancel();
            return { kind: "terminal", outcome: { kind: "done", event } };
          }
          if (event.event === "error") {
            await reader.cancel();
            return { kind: "terminal", outcome: { kind: "error", problem: event.data } };
          }
        }
      }
    } catch (error) {
      if (options.signal?.aborted) throw error;
    }
    // The body ended or broke without a terminal event.
    return { kind: "retry", progressed };
  };

  let attempt = 0;
  for (;;) {
    let result: ConnectionResult;
    try {
      result = await connect();
    } catch {
      if (options.signal?.aborted) return { kind: "aborted" };
      result = { kind: "retry", progressed: false };
    }
    if (result.kind === "terminal") return result.outcome;
    if (options.signal?.aborted) return { kind: "aborted" };
    if (!resumable) return fail(connectionProblem("Connection lost"), "failed");
    if (result.progressed) attempt = 0;
    if (attempt >= maxRetries) return fail(connectionProblem("Connection lost"), "failed");

    const backoff = Math.max(parser.retryMs ?? 0, baseDelay * 2 ** attempt);
    const jittered = Math.min(maxDelay, backoff) * (0.5 + 0.5 * random());
    await sleep(
      result.waitMs ? Math.min(maxDelay, result.waitMs) : Math.round(jittered),
      options.signal,
    );
    if (options.signal?.aborted) return { kind: "aborted" };
    attempt++;
  }
}
