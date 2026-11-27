"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { useAuth } from "@/lib/auth";
import {
  applyGenerationEvent,
  applyStepEvent,
  type GenerationState,
  initialGenerationState,
  initialStepState,
  openEventStream,
  type Step,
  type StreamEvent,
  type StreamOutcome,
} from "@/lib/sse";

export type ChatStreamPhase = "idle" | "streaming" | "done" | "error" | "stopped";

export interface ChatStream {
  phase: ChatStreamPhase;
  reply: GenerationState;
  /** The reply's tool calls (`agent` = `task-chat`), keyed by `seq`. */
  steps: Step[];
}

type Action =
  | { type: "start" }
  | { type: "event"; event: StreamEvent }
  | { type: "stopped" }
  | { type: "reset" };

const idle: ChatStream = { phase: "idle", reply: initialGenerationState(), steps: [] };

function reducer(current: ChatStream, action: Action): ChatStream {
  switch (action.type) {
    case "start":
      return { phase: "streaming", reply: initialGenerationState(), steps: [] };
    case "event": {
      if (current.phase !== "streaming") return current;
      const reply = applyGenerationEvent(current.reply, action.event);
      const { steps } = applyStepEvent(
        { ...initialStepState(), steps: current.steps },
        action.event,
      );
      return { phase: reply.status === "streaming" ? "streaming" : reply.status, reply, steps };
    }
    case "stopped":
      return current.phase === "streaming" ? { ...current, phase: "stopped" } : current;
    case "reset":
      return idle;
  }
}

/**
 * One task-chat reply at a time: segments and retractions (always "[redacted]") and the tool
 * steps are applied as they arrive; `stop()` aborts the request. A POST is never re-sent.
 */
export function useTaskChatStream(baseUrl?: string) {
  const { adapter } = useAuth();
  const [stream, dispatch] = useReducer(reducer, idle);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(
    async (taskId: string, content: string): Promise<StreamOutcome> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "start" });
      const outcome = await openEventStream({
        path: `/tasks/${encodeURIComponent(taskId)}/chat`,
        method: "POST",
        body: { content },
        getToken: () => adapter.getToken(),
        signal: controller.signal,
        ...(baseUrl ? { baseUrl } : {}),
        onEvent: (event) => {
          if (!controller.signal.aborted) dispatch({ type: "event", event });
        },
      });
      if (outcome.kind === "aborted") dispatch({ type: "stopped" });
      if (abortRef.current === controller) abortRef.current = null;
      return outcome;
    },
    [adapter, baseUrl],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "stopped" });
  }, []);

  const reset = useCallback(() => dispatch({ type: "reset" }), []);

  return { ...stream, start, stop, reset };
}
