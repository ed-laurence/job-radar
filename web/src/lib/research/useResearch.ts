"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useAnalytics } from "@/lib/AnalyticsProvider";
import { type Problem, type Schemas, unwrap, useApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  applyStepEvent,
  initialStepState,
  openEventStream,
  type Step,
  type StepState,
  type StreamEvent,
} from "@/lib/sse";

export type ResearchFocus = NonNullable<Schemas["ResearchRunCreate"]["focus"]>[number];
export type ResearchRun = Schemas["ResearchRun"];

export const researchKeys = {
  run: (runId: string | undefined) => ["research-runs", runId] as const,
};

export function useStartResearch() {
  const client = useApiClient();
  return useMutation({
    mutationFn: ({ targetId, focus }: { targetId: string; focus: readonly ResearchFocus[] }) =>
      unwrap(
        client.POST("/targets/{targetId}/research-runs", {
          params: { path: { targetId } },
          body: focus.length ? { focus: [...focus] } : {},
        }),
      ),
  });
}

/** The finished run's summary and sources; fetched once the step stream reports `done`. */
export function useResearchRun(runId: string | undefined, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: researchKeys.run(runId),
    enabled: enabled && Boolean(runId),
    queryFn: () =>
      unwrap(
        client.GET("/research-runs/{researchRunId}", {
          params: { path: { researchRunId: runId ?? "" } },
        }),
      ),
  });
}

type Action = { type: "start" } | { type: "event"; event: StreamEvent };

interface Watch {
  active: boolean;
  state: StepState;
}

function reducer(current: Watch, action: Action): Watch {
  if (action.type === "start") return { active: true, state: initialStepState() };
  return { ...current, state: applyStepEvent(current.state, action.event) };
}

/** Watches a research run's steps. GET streams resume with `Last-Event-ID`, so drops are healed. */
export function useResearchSteps() {
  const { adapter } = useAuth();
  const [watch, dispatch] = useReducer(reducer, { active: false, state: initialStepState() });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const watchRun = useCallback(
    async (runId: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "start" });
      return openEventStream({
        path: `/research-runs/${encodeURIComponent(runId)}/steps`,
        getToken: () => adapter.getToken(),
        signal: controller.signal,
        onEvent: (event) => {
          if (!controller.signal.aborted) dispatch({ type: "event", event });
        },
      });
    },
    [adapter],
  );

  return { ...watch, watchRun };
}

export interface Research {
  start: (targetId: string, focus: readonly ResearchFocus[]) => void;
  running: boolean;
  finished: boolean;
  /** Why the run couldn't start (a failed POST), if it couldn't. */
  startError: unknown;
  /** The stream's terminal problem, if the run failed. */
  streamError: Problem | undefined;
  failed: boolean;
  /** Null until a run starts. */
  steps: Step[] | null;
  run: ResearchRun | undefined;
}

/** One research run end to end: start it, stream its steps, then load the summary. */
export function useResearch(): Research {
  const { track } = useAnalytics();
  const start = useStartResearch();
  const watch = useResearchSteps();
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const finished = watch.active && watch.state.status === "done";
  const run = useResearchRun(runId, finished);
  const { mutate } = start;
  const { watchRun } = watch;

  const begin = useCallback(
    (targetId: string, focus: readonly ResearchFocus[]) =>
      mutate(
        { targetId, focus },
        {
          onSuccess: (created) => {
            track("research_started");
            setRunId(created.id);
            void watchRun(created.id);
          },
        },
      ),
    [mutate, track, watchRun],
  );

  const failed = watch.active && watch.state.status === "error";
  return {
    start: begin,
    running: start.isPending || (watch.active && watch.state.status === "streaming"),
    finished,
    startError: start.isError ? start.error : undefined,
    streamError: failed ? watch.state.error : undefined,
    failed,
    steps: watch.active ? watch.state.steps : null,
    run: run.data,
  };
}
