"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";
import { type ExperienceList, experienceQueryKey } from "@/lib/experience";
import { settingsQueryKey } from "@/lib/settings";

export { useCreateExperience, useExperienceList as useExperience } from "@/lib/experience";

import { readFileAsBase64 } from "./files";
import type { Settings, SettingsUpdate } from "./model";

/** Saves the settings with the version that was read (409 when it is stale). */
export function useSaveSettings() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => unwrap(client.PUT("/settings", { body })),
    onSuccess: (saved) => queryClient.setQueryData(settingsQueryKey, saved),
  });
}

/** Reads the latest settings straight from the server (after a version conflict). */
export function useFetchLatestSettings() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return (): Promise<Settings> =>
    queryClient.fetchQuery({
      queryKey: settingsQueryKey,
      queryFn: () => unwrap(client.GET("/settings")),
      staleTime: 0,
    });
}

// ---- CVs and the LinkedIn profile PDF ----

export type DocumentSlot = Schemas["DocumentSlot"];
export type ProfileDocument = Schemas["ProfileDocument"];
export type ExperienceRecord = Schemas["ExperienceRecord"];
export type LinkedinImportSummary = Schemas["LinkedinImportSummary"];
export type ContactsImportResult = Schemas["ContactsImportResult"];

export const documentsKey = ["profile", "documents"] as const;
export const experienceKey = experienceQueryKey;
export const linkedinImportKey = ["profile", "linkedin-import"] as const;
/** The Contacts page's queries; invalidated after an import or a remove-all. */
const contactsKey = ["contacts"] as const;

export function useProfileDocuments() {
  const client = useApiClient();
  return useQuery({
    queryKey: documentsKey,
    queryFn: () => unwrap(client.GET("/profile/documents")),
  });
}

export interface UploadInput {
  slot: DocumentSlot;
  file: File;
  onProgress: (fraction: number) => void;
}

/** Reads the PDF as base64 (progress is the read; the send is reported as the last step). */
export function useUploadDocument() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ slot, file, onProgress }: UploadInput) => {
      const data = await readFileAsBase64(file, (f) => onProgress(f * 0.9));
      return unwrap(
        client.PUT("/profile/documents/{slot}", {
          params: { path: { slot } },
          body: { filename: file.name.slice(0, 200), data },
        }),
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  });
}

export function useRemoveDocument() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slot: DocumentSlot) =>
      unwrap(client.DELETE("/profile/documents/{slot}", { params: { path: { slot } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  });
}

/**
 * Fetches the PDF through the BFF with the caller's token and returns an object URL for it. A
 * plain link can't carry the bearer token, so the file is fetched first and opened as a blob.
 */
export function useDocumentFileUrl() {
  const client = useApiClient();
  return async (slot: DocumentSlot): Promise<string> => {
    const blob = await unwrap(
      client.GET("/profile/documents/{slot}/file", {
        params: { path: { slot } },
        parseAs: "blob",
      }),
    );
    return URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
  };
}

// ---- Experience not on your CV ----

export function useUpdateExperience() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["ExperienceUpdate"] }) =>
      unwrap(
        client.PUT("/experience/{experienceId}", {
          params: { path: { experienceId: id } },
          body,
        }),
      ),
    // Show the saved record (and its new version) at once; the refetch brings the use counts.
    onSuccess: (saved) => {
      queryClient.setQueryData<ExperienceList>(experienceKey, (old) =>
        old ? { ...old, items: old.items.map((r) => (r.id === saved.id ? saved : r)) } : old,
      );
      return queryClient.invalidateQueries({ queryKey: experienceKey });
    },
  });
}

export function useDeleteExperience() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      unwrap(
        client.DELETE("/experience/{experienceId}", {
          params: { path: { experienceId: id }, query: { version } },
        }),
      ),
    // A 409 means someone changed it; reloading shows the latest either way.
    onSettled: () => queryClient.invalidateQueries({ queryKey: experienceKey }),
  });
}

// ---- LinkedIn connections ----

export function useLinkedinImport() {
  const client = useApiClient();
  return useQuery({
    queryKey: linkedinImportKey,
    queryFn: () => unwrap(client.GET("/profile/linkedin-import")),
  });
}

export function useImportContacts() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, onProgress }: { file: File; onProgress: (f: number) => void }) => {
      const data = await readFileAsBase64(file, (f) => onProgress(f * 0.9));
      return unwrap(
        client.POST("/contacts/import", { body: { filename: file.name.slice(0, 200), data } }),
      );
    },
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: linkedinImportKey }),
        queryClient.invalidateQueries({ queryKey: contactsKey }),
      ]),
  });
}

export function useRemoveAllContacts() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(client.DELETE("/contacts", { body: { confirm: true } })),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: linkedinImportKey }),
        queryClient.invalidateQueries({ queryKey: contactsKey }),
      ]),
  });
}
