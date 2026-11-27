"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";

export type ExperienceRecord = Schemas["ExperienceRecord"];
export type ExperienceCreate = Schemas["ExperienceCreate"];
export type ExperienceList = Schemas["ExperienceList"];

/** The one cache key for `GET /experience`, shared by Settings and the evidence block. */
export const experienceQueryKey = ["experience", "list"] as const;

/** The records and the suggested "Where" contexts (the roles on your CVs, then the fallbacks). */
export function useExperienceList(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: experienceQueryKey,
    enabled,
    queryFn: () => unwrap(client.GET("/experience")),
  });
}

/** Adds an experience record and refreshes the Settings list. */
export function useCreateExperience() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ExperienceCreate) => unwrap(client.POST("/experience", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: experienceQueryKey }),
  });
}
