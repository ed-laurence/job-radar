"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  describeError,
  type ErrorView,
  isApiError,
  isNotFound,
  type Schemas,
  unwrap,
  useApiClient,
} from "@/lib/api";
import { useCreateExperience, useExperienceList } from "@/lib/experience";
import { useUserSettings } from "@/lib/settings";

export type EvidenceTest = Schemas["RoleEvidenceTest"];
export type EvidenceTestItem = Schemas["EvidenceTestItem"];
export type ExperienceDraft = Schemas["ExperienceCreate"];

/** How long "Run again" stays disabled after the AI rate limit is hit. */
export const RATE_LIMIT_COOLDOWN_MS = 30_000;

export const evidenceKeys = {
  all: ["evidence"] as const,
  role: (roleId: string) => ["evidence", roleId] as const,
};

/** Queries that show a role's evidence summary (role lists, role detail, dashboard cards). */
const SUMMARY_KEYS = [["roles"], ["dashboard", "cards"]] as const;

/** The latest full evidence test; `null` when the role has never been checked (404). */
export function useRoleEvidence(roleId: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: evidenceKeys.role(roleId),
    enabled,
    queryFn: async (): Promise<EvidenceTest | null> => {
      try {
        return await unwrap(
          client.GET("/roles/{roleId}/evidence", { params: { path: { roleId } } }),
        );
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },
  });
}

/** Runs the evidence test now, stores the result in the cache and refreshes the summaries. */
export function useRunEvidence(roleId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(client.POST("/roles/{roleId}/evidence", { params: { path: { roleId } } })),
    onSuccess: (test) => {
      queryClient.setQueryData(evidenceKeys.role(roleId), test);
      for (const queryKey of SUMMARY_KEYS) void queryClient.invalidateQueries({ queryKey });
    },
  });
}

export function isRateLimited(error: unknown): boolean {
  return (
    isApiError(error) && error.status === 429 && error.problem.type !== "/problems/budget-exceeded"
  );
}

/** A failed run, worded for the evidence block (the AI limits get their own wording). */
export function describeRunError(error: unknown): ErrorView {
  const view = describeError(error, "The evidence test didn't run");
  if (!isApiError(error)) return view;
  if (error.status === 429 && error.problem.type === "/problems/budget-exceeded") {
    return {
      ...view,
      title: "Today's AI budget is used up",
      description: "The evidence test can run again tomorrow. The last result stays as it is.",
    };
  }
  if (error.status === 429) {
    return {
      ...view,
      title: "The AI is busy",
      description: "Too many AI requests in a short time. Wait a moment, then run it again.",
    };
  }
  if (error.status === 422) {
    return {
      ...view,
      title: "This role can't be tested",
      description: error.problem.detail ?? "It has no ad text to check against your CV.",
    };
  }
  return view;
}

export interface EvidenceController {
  /** The full test: `undefined` until loaded, `null` when never run. */
  test: EvidenceTest | null | undefined;
  loading: boolean;
  loadError: ErrorView | undefined;
  onRetryLoad: () => void;
  running: boolean;
  /** Run again is paused after the AI rate limit. */
  cooling: boolean;
  runError: ErrorView | undefined;
  onRun: () => void;
  /** Announced politely (saved, re-run done). */
  notice: string;
  timeZone: string | undefined;
  /** The requirement "I've done this" was clicked on, while its dialog is open. */
  doneFor: string | null;
  onDone: (item: EvidenceTestItem) => void;
  onCancelDone: () => void;
  saving: boolean;
  saveError: ErrorView | undefined;
  /** Saves the record for this role (the role id is added here), then runs the test again. */
  onSaveDone: (record: ExperienceDraft) => void;
  /** The "Where" suggestions for the record's rows (loaded when "I've done this" opens). */
  contexts: readonly string[];
}

/**
 * Everything the evidence block needs for one role: the lazy GET (when `enabled`), Run again with
 * the AI-limit handling, and "I've done this" (save a record, then run the test again).
 */
export function useEvidence(roleId: string, enabled: boolean): EvidenceController {
  const query = useRoleEvidence(roleId, enabled);
  const run = useRunEvidence(roleId);
  const create = useCreateExperience();
  const settings = useUserSettings();
  const [doneFor, setDoneFor] = useState<string | null>(null);
  const experience = useExperienceList(doneFor !== null);
  const [notice, setNotice] = useState("");
  const [coolUntil, setCoolUntil] = useState(0);

  useEffect(() => {
    if (!coolUntil) return;
    const timer = window.setTimeout(() => setCoolUntil(0), Math.max(0, coolUntil - Date.now()));
    return () => window.clearTimeout(timer);
  }, [coolUntil]);

  const runNow = (prefix = "") => {
    setNotice(prefix ? `${prefix} Running the evidence test again…` : "");
    run.mutate(undefined, {
      onSuccess: (test) =>
        setNotice(
          `${prefix ? `${prefix} ` : ""}Evidence test updated: you show ${test.shown} of ${test.total} must-haves.`,
        ),
      onError: (error) => {
        setNotice("");
        if (isRateLimited(error)) setCoolUntil(Date.now() + RATE_LIMIT_COOLDOWN_MS);
      },
    });
  };

  return {
    test: query.data,
    loading: enabled && query.isPending,
    loadError: query.isError
      ? describeError(query.error, "The evidence failed to load")
      : undefined,
    onRetryLoad: () => void query.refetch(),
    running: run.isPending,
    cooling: coolUntil > 0,
    runError: run.isError ? describeRunError(run.error) : undefined,
    onRun: () => runNow(),
    notice,
    timeZone: settings.data?.display.timezone,
    doneFor,
    onDone: (item) => {
      create.reset();
      setDoneFor(item.requirement);
    },
    onCancelDone: () => setDoneFor(null),
    saving: create.isPending,
    saveError: create.isError
      ? describeError(create.error, "The record couldn't be saved")
      : undefined,
    onSaveDone: (record) =>
      create.mutate(
        { ...record, role_id: roleId },
        {
          onSuccess: (saved) => {
            setDoneFor(null);
            runNow(`Saved ${saved.requirement} to your records.`);
          },
        },
      ),
    contexts: experience.data?.contexts ?? [],
  };
}
