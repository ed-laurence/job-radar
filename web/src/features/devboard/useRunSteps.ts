"use client";

import { useEffect, useReducer, useRef } from "react";
import { DEFAULT_API_BASE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { applyStepEvent, initialStepState, openEventStream, type StepState } from "@/lib/sse";

export interface RunStepsOptions {
  enabled: boolean;
  baseUrl?: string | undefined;
  /** Called once when the stream ends with `done` (the run finished). */
  onFinished?: () => void;
}

/**
 * Live steps of one run over SSE (`GET /runs/{id}/steps`): saved steps replay first, then new
 * ones stream. Steps are keyed by `seq`, so reconnecting or re-expanding never duplicates one.
 */
export function useRunSteps(runId: string, options: RunStepsOptions): StepState {
  const { adapter } = useAuth();
  const [state, dispatch] = useReducer(applyStepEvent, undefined, initialStepState);
  const onFinished = useRef(options.onFinished);
  onFinished.current = options.onFinished;
  const { enabled, baseUrl = DEFAULT_API_BASE } = options;

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void openEventStream({
      path: `/runs/${encodeURIComponent(runId)}/steps`,
      baseUrl,
      getToken: () => adapter.getToken(),
      onEvent: dispatch,
      signal: controller.signal,
    }).then((outcome) => {
      if (outcome.kind === "done" && !controller.signal.aborted) onFinished.current?.();
    });
    return () => controller.abort();
  }, [runId, enabled, baseUrl, adapter]);

  return state;
}
