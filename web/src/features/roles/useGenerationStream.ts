"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { useAuth } from "@/lib/auth";
import {
  applyGenerationEvent,
  type GenerationState,
  initialGenerationState,
  openEventStream,
  type StreamEvent,
  type StreamOutcome,
} from "@/lib/sse";

export type StreamPhase = "idle" | "streaming" | "done" | "error" | "stopped";

export interface GenerationStream {
  phase: StreamPhase;
  state: GenerationState;
}

type Action =
  | { type: "start" }
  | { type: "event"; event: StreamEvent }
  | { type: "stopped" }
  | { type: "reset" };

const idle: GenerationStream = { phase: "idle", state: initialGenerationState() };

function reducer(current: GenerationStream, action: Action): GenerationStream {
  switch (action.type) {
    case "start":
      return { phase: "streaming", state: initialGenerationState() };
    case "event": {
      if (current.phase !== "streaming") return current;
      const state = applyGenerationEvent(current.state, action.event);
      const phase = state.status === "streaming" ? "streaming" : state.status;
      return { phase, state };
    }
    case "stopped":
      return current.phase === "streaming" ? { ...current, phase: "stopped" } : current;
    case "reset":
      return idle;
  }
}

/**
 * One kit or chat generation at a time: `segment`/`retract` are applied as they arrive (a
 * retraction always shows "[redacted]"), and `stop()` aborts the request.
 */
export function useGenerationStream() {
  const { adapter } = useAuth();
  const [stream, dispatch] = useReducer(reducer, idle);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(
    async (path: string, body: unknown): Promise<StreamOutcome> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "start" });
      const outcome = await openEventStream({
        path,
        method: "POST",
        body,
        getToken: () => adapter.getToken(),
        signal: controller.signal,
        onEvent: (event) => {
          if (!controller.signal.aborted) dispatch({ type: "event", event });
        },
      });
      if (outcome.kind === "aborted") dispatch({ type: "stopped" });
      if (abortRef.current === controller) abortRef.current = null;
      return outcome;
    },
    [adapter],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "stopped" });
  }, []);

  const reset = useCallback(() => dispatch({ type: "reset" }), []);

  return { ...stream, start, stop, reset };
}
